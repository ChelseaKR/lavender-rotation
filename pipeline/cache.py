"""Local-first SQLite cache with data-lineage timestamps and a managed lifecycle.

Privacy posture (RESPONSIBLE-TECH-AUDITS §C): listening data and enriched
metadata live in a single on-disk SQLite file under ``data/`` and never leave the
machine. There is no telemetry and no third-party client here — only stdlib
``sqlite3``. Every cached row carries a ``fetched_at`` timestamp so each record is
traceable to a source + fetch time (Quality §9, data quality & lineage).

Lifecycle (FIX-04): the cache is a *managed* local datastore, not an append-only
scratchpad:

* **Dedupe** — scrobbles carry a ``UNIQUE(username, artist_id, track, ts)`` key and
  are inserted ``OR IGNORE``, so re-ingesting the same history is byte-identical in
  the DB (no duplicate play weights).
* **TTL** — cached HTTP responses can be treated as stale past a TTL. This is the
  storage primitive a future live enricher needs; the shipped demo CLI does not
  fetch corrected upstream identity claims.
* **Schema versioning** — ``PRAGMA user_version`` plus a tiny forward-only migration
  runner. Opening an *older* cache migrates it in place; opening a *newer* one fails
  loudly rather than silently misreading.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Optional

from recommender.feedback import Feedback

from pipeline.identity import IdentityEvidence
from pipeline.models import Artist, Scrobble, SourceKind, UnsourcedIdentityError
from pipeline.paths import default_db_path
from pipeline.serde import artist_from_dict, artist_to_dict

DEFAULT_DB_PATH = default_db_path()

#: Current on-disk schema version. Bump when a migration is added below.
CACHE_SCHEMA_VERSION = 4

#: Default staleness horizon for cached HTTP responses / identity claims, in days.
#: Applied by callers that pass ``ttl_days`` (or ``lavender refresh``); the default is
#: conservative and re-checks on a cadence matching ``identity-data-ethics.md``'s
#: "recheck per identity-source API change".
DEFAULT_HTTP_TTL_DAYS = 30

# Base tables (schema v1). ``scrobbles`` has no uniqueness here; v2 adds it after
# de-duplicating any legacy rows. Every statement is ``IF NOT EXISTS`` so applying
# v1 over an already-populated legacy cache is a safe no-op.
_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS artists (
    artist_id  TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scrobbles (
    username    TEXT NOT NULL,
    artist_id   TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    track       TEXT NOT NULL,
    ts          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scrobbles_user ON scrobbles(username);
CREATE TABLE IF NOT EXISTS http_cache (
    url        TEXT PRIMARY KEY,
    body       TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""

# v2: drop duplicate scrobble rows (keep the earliest rowid per key), then enforce
# the dedupe key so re-ingest is idempotent going forward.
_MIGRATE_V2 = """
DELETE FROM scrobbles WHERE rowid NOT IN (
    SELECT MIN(rowid) FROM scrobbles
    GROUP BY username, artist_id, track, ts
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scrobbles_dedupe
    ON scrobbles(username, artist_id, track, ts);
"""

# v3 (FIX-10): cited local corrections persist independently of HTTP cache TTL.
_MIGRATE_V3 = """
CREATE TABLE IF NOT EXISTS corrections (
    artist_id      TEXT NOT NULL,
    asserted_value TEXT NOT NULL,
    citation       TEXT NOT NULL,
    retrieved_at   TEXT NOT NULL,
    entered_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corrections_artist ON corrections(artist_id);
"""

# v4: one current thumbs vote per listener and artist. Re-voting replaces the
# prior value while retaining when the latest opinion was recorded.
_MIGRATE_V4 = """
CREATE TABLE IF NOT EXISTS feedback (
    username   TEXT NOT NULL,
    artist_id  TEXT NOT NULL,
    vote       INTEGER NOT NULL CHECK(vote IN (-1, 1)),
    ts         INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(username, artist_id)
);
"""


class CacheSchemaError(RuntimeError):
    """Raised when a cache file's schema is newer than this code can safely read."""


def _migrate_to_v1(conn: sqlite3.Connection) -> None:
    conn.executescript(_BASE_SCHEMA)


def _migrate_to_v2(conn: sqlite3.Connection) -> None:
    conn.executescript(_MIGRATE_V2)


def _migrate_to_v3(conn: sqlite3.Connection) -> None:
    conn.executescript(_MIGRATE_V3)


def _migrate_to_v4(conn: sqlite3.Connection) -> None:
    conn.executescript(_MIGRATE_V4)


#: Forward-only migrations keyed by the schema version they *produce*.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_to_v1,
    2: _migrate_to_v2,
    3: _migrate_to_v3,
    4: _migrate_to_v4,
}


def _parse_iso_date(value: str) -> Optional[date]:
    """Parse the date portion of an ISO ``fetched_at`` string, or ``None`` if invalid."""
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


class Cache:
    """A thin, typed wrapper over a local SQLite database.

    Use as a context manager so the connection is always closed::

        with Cache(":memory:") as cache:
            cache.put_artist(artist, fetched_at="2026-05-31")

    Opening a cache runs any pending :data:`_MIGRATIONS`; a cache written by a newer
    version of the code raises :class:`CacheSchemaError` rather than misreading it.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        # Ensure the parent directory exists regardless of whether the caller
        # passed a Path or a plain str (the CLI passes ``--db`` as a str, so a
        # Path-only check here previously left `data/` uncreated and crashed
        # with sqlite3.OperationalError on a fresh clone). ``:memory:``'s
        # "parent" is just ``.``, which always exists, so this is a no-op there.
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    # -- schema lifecycle ----------------------------------------------------
    def _migrate(self) -> None:
        current = self.schema_version
        if current > CACHE_SCHEMA_VERSION:
            self.conn.close()
            raise CacheSchemaError(
                f"cache schema v{current} is newer than supported v{CACHE_SCHEMA_VERSION}; "
                "upgrade lavender-rotation or start from a fresh cache"
            )
        with closing(self.conn.cursor()) as cur:
            for target in range(current + 1, CACHE_SCHEMA_VERSION + 1):
                _MIGRATIONS[target](self.conn)
            if current < CACHE_SCHEMA_VERSION:
                # PRAGMA cannot be parameterised; the value is a trusted int constant.
                cur.execute(f"PRAGMA user_version = {int(CACHE_SCHEMA_VERSION)}")
        self.conn.commit()

    @property
    def schema_version(self) -> int:
        row = self.conn.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self) -> Cache:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # -- artists -------------------------------------------------------------
    def put_artist(self, artist: Artist, fetched_at: str) -> None:
        self.conn.execute(
            "INSERT INTO artists(artist_id, payload, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(artist_id) DO UPDATE SET payload=excluded.payload, "
            "fetched_at=excluded.fetched_at",
            (artist.artist_id, json.dumps(artist_to_dict(artist)), fetched_at),
        )
        self.conn.commit()

    def get_artist(self, artist_id: str) -> Optional[Artist]:
        row = self.conn.execute(
            "SELECT payload FROM artists WHERE artist_id = ?", (artist_id,)
        ).fetchone()
        if row is None:
            return None
        return artist_from_dict(json.loads(row["payload"]))

    def artist_fetched_at(self, artist_id: str) -> Optional[str]:
        """Return when an artist was last enriched — the lineage timestamp."""
        row = self.conn.execute(
            "SELECT fetched_at FROM artists WHERE artist_id = ?", (artist_id,)
        ).fetchone()
        return row["fetched_at"] if row else None

    def list_artist_ids(self) -> list[str]:
        """Every cached artist id available to a dependency-injected refresh."""
        rows = self.conn.execute("SELECT artist_id FROM artists").fetchall()
        return [row["artist_id"] for row in rows]

    # -- scrobbles -----------------------------------------------------------
    def put_scrobbles(self, username: str, scrobbles: Iterable[Scrobble]) -> None:
        """Persist scrobbles idempotently (dedupe key: username, artist_id, track, ts)."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO scrobbles(username, artist_id, artist_name, track, ts) "
            "VALUES (?, ?, ?, ?, ?)",
            [(username, s.artist_id, s.artist_name, s.track, s.ts) for s in scrobbles],
        )
        self.conn.commit()

    def last_synced_ts(self, username: str) -> int:
        """Return the newest stored scrobble ``ts`` for ``username``, or 0 if none.

        This is the since-cursor for incremental, resumable ingest (FIX-02):
        the caller passes it back in as ``since_ts`` so only new plays get
        fetched. Derived from ``MAX(ts)`` rather than a separate watermark
        table — the stored history is already the source of truth, and this
        keeps the cache schema unchanged.
        """
        row = self.conn.execute(
            "SELECT MAX(ts) AS max_ts FROM scrobbles WHERE username = ?", (username,)
        ).fetchone()
        max_ts = row["max_ts"] if row else None
        return int(max_ts) if max_ts is not None else 0

    def get_scrobbles(self, username: str) -> list[Scrobble]:
        rows = self.conn.execute(
            "SELECT artist_id, artist_name, track, ts FROM scrobbles "
            "WHERE username = ? ORDER BY ts",
            (username,),
        ).fetchall()
        return [
            Scrobble(
                artist_id=r["artist_id"],
                artist_name=r["artist_name"],
                track=r["track"],
                ts=r["ts"],
            )
            for r in rows
        ]

    # -- http response cache (rate-limit respect + TTL) ----------------------
    def _is_stale(self, fetched_at: str, ttl_days: int, now: Optional[str]) -> bool:
        """True if a row fetched at ``fetched_at`` is older than ``ttl_days`` at ``now``."""
        fetched = _parse_iso_date(fetched_at)
        if fetched is None:
            return True  # unparseable lineage → re-fetch rather than trust it
        reference = _parse_iso_date(now) if now is not None else date.today()
        if reference is None:
            return True
        return (reference - fetched).days > ttl_days

    def get_cached_response(
        self, url: str, *, ttl_days: Optional[int] = None, now: Optional[str] = None
    ) -> Optional[str]:
        """Return a cached body, or ``None`` on a miss — or if it is older than ``ttl_days``.

        With ``ttl_days=None`` (the default) responses never expire, preserving the
        original rate-limit-respecting behaviour. A caller re-checking identity
        claims passes a TTL so a stale claim is treated as a miss and re-fetched.
        """
        row = self.conn.execute(
            "SELECT body, fetched_at FROM http_cache WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            return None
        if ttl_days is not None and self._is_stale(row["fetched_at"], ttl_days, now):
            return None
        return str(row["body"])

    def put_cached_response(self, url: str, body: str, fetched_at: str) -> None:
        self.conn.execute(
            "INSERT INTO http_cache(url, body, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET body=excluded.body, "
            "fetched_at=excluded.fetched_at",
            (url, body, fetched_at),
        )
        self.conn.commit()

    def expire_http_cache(self, *, ttl_days: int, now: Optional[str] = None) -> int:
        """Delete cached responses older than ``ttl_days``. Returns the number removed.

        This makes a subsequent live client call miss the cache. The shipped
        ``lavender refresh`` command is fixture-only and does not make that client call.
        """
        rows = self.conn.execute("SELECT url, fetched_at FROM http_cache").fetchall()
        stale = [r["url"] for r in rows if self._is_stale(r["fetched_at"], ttl_days, now)]
        self.conn.executemany("DELETE FROM http_cache WHERE url = ?", [(u,) for u in stale])
        self.conn.commit()
        return len(stale)

    # -- listener feedback -------------------------------------------------
    def record_feedback(self, feedback: Feedback, fetched_at: str) -> None:
        """Store the listener's current vote for an artist."""
        self.conn.execute(
            "INSERT INTO feedback(username, artist_id, vote, ts, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(username, artist_id) DO UPDATE SET "
            "vote=excluded.vote, ts=excluded.ts, updated_at=excluded.updated_at",
            (
                feedback.username,
                feedback.artist_id,
                feedback.vote,
                feedback.ts,
                fetched_at,
            ),
        )
        self.conn.commit()

    def load_feedback(self, username: str) -> list[Feedback]:
        """Load one current vote per artist in deterministic order."""
        rows = self.conn.execute(
            "SELECT username, artist_id, vote, ts FROM feedback "
            "WHERE username = ? ORDER BY ts, artist_id",
            (username,),
        ).fetchall()
        return [
            Feedback(
                username=row["username"],
                artist_id=row["artist_id"],
                vote=row["vote"],
                ts=row["ts"],
            )
            for row in rows
        ]

    # -- local corrections ledger (FIX-10) ---------------------------------
    def put_correction(self, artist_id: str, evidence: IdentityEvidence, entered_at: str) -> None:
        """Record a cited artist-statement correction."""
        source = evidence.as_source()
        if source.kind is not SourceKind.ARTIST_STATEMENT:
            raise UnsourcedIdentityError(
                "a correction must be recorded as an ARTIST_STATEMENT source"
            )
        self.conn.execute(
            "INSERT INTO corrections"
            "(artist_id, asserted_value, citation, retrieved_at, entered_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (artist_id, evidence.value, evidence.citation, evidence.retrieved_at, entered_at),
        )
        self.conn.commit()

    def get_corrections(self, artist_id: str) -> tuple[IdentityEvidence, ...]:
        """Return corrections for one artist in deterministic order."""
        rows = self.conn.execute(
            "SELECT asserted_value, citation, retrieved_at FROM corrections "
            "WHERE artist_id = ? ORDER BY entered_at, citation",
            (artist_id,),
        ).fetchall()
        return tuple(
            IdentityEvidence(
                kind=SourceKind.ARTIST_STATEMENT,
                value=row["asserted_value"],
                citation=row["citation"],
                retrieved_at=row["retrieved_at"],
                is_local_correction=True,
            )
            for row in rows
        )

    def list_corrections(self) -> tuple[tuple[str, IdentityEvidence, str], ...]:
        """Return every correction as ``(artist_id, evidence, entered_at)``."""
        rows = self.conn.execute(
            "SELECT artist_id, asserted_value, citation, retrieved_at, entered_at "
            "FROM corrections ORDER BY entered_at, citation"
        ).fetchall()
        return tuple(
            (
                row["artist_id"],
                IdentityEvidence(
                    kind=SourceKind.ARTIST_STATEMENT,
                    value=row["asserted_value"],
                    citation=row["citation"],
                    retrieved_at=row["retrieved_at"],
                    is_local_correction=True,
                ),
                row["entered_at"],
            )
            for row in rows
        )
