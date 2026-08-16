"""FIX-04: cache lifecycle — dedupe, TTL, schema versioning/migrations, refresh.

The cache backs *identity claims*, so its lifecycle is a responsible-tech surface:
a stale cached claim must be re-checkable (TTL / ``lavender refresh``), re-ingesting the
same history must not double play weights (dedupe), and a schema mismatch must fail
loudly rather than silently misread (versioning).
"""

from __future__ import annotations

import sqlite3

import pytest
from pipeline.cache import (
    CACHE_SCHEMA_VERSION,
    Cache,
    CacheSchemaError,
)
from pipeline.cli import main as cli_main
from pipeline.ingest import refresh_catalog
from pipeline.models import Gender, Scrobble

from .conftest import make_artist


@pytest.fixture
def mem_cache():
    cache = Cache(":memory:")
    yield cache
    cache.close()


# -- schema versioning / migrations -------------------------------------------


def test_fresh_cache_is_stamped_with_current_schema_version(tmp_path) -> None:
    with Cache(tmp_path / "cache.db") as cache:
        assert cache.schema_version == CACHE_SCHEMA_VERSION


def test_legacy_unversioned_cache_migrates_in_place_and_dedupes(tmp_path) -> None:
    """A pre-versioning cache (user_version=0) with duplicate scrobbles is repaired."""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE artists (artist_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                              fetched_at TEXT NOT NULL);
        CREATE TABLE scrobbles (username TEXT NOT NULL, artist_id TEXT NOT NULL,
                                artist_name TEXT NOT NULL, track TEXT NOT NULL,
                                ts INTEGER NOT NULL);
        CREATE TABLE http_cache (url TEXT PRIMARY KEY, body TEXT NOT NULL,
                                 fetched_at TEXT NOT NULL);
        """
    )
    dup = ("u", "mitski", "Mitski", "Geyser", 100)
    conn.executemany(
        "INSERT INTO scrobbles(username, artist_id, artist_name, track, ts) VALUES (?, ?, ?, ?, ?)",
        [dup, dup, ("u", "mitski", "Mitski", "Geyser", 200)],
    )
    conn.commit()
    conn.close()

    with Cache(db) as cache:
        assert cache.schema_version == CACHE_SCHEMA_VERSION
        loaded = cache.get_scrobbles("u")
        assert [s.ts for s in loaded] == [100, 200]  # duplicate row collapsed


def test_cache_from_a_newer_version_fails_loudly(tmp_path) -> None:
    db = tmp_path / "future.db"
    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()
    with pytest.raises(CacheSchemaError):
        Cache(db)


# -- dedupe (idempotent re-ingest) ---------------------------------------------


def test_reingesting_the_same_scrobbles_is_idempotent(tmp_path) -> None:
    scrobbles = [
        Scrobble("mitski", "Mitski", "Geyser", 100),
        Scrobble("mitski", "Mitski", "Nobody", 200),
    ]
    with Cache(tmp_path / "cache.db") as cache:
        cache.put_scrobbles("u", scrobbles)
        cache.put_scrobbles("u", scrobbles)  # re-ingest of the same history
        assert len(cache.get_scrobbles("u")) == 2  # no duplicate play weights


# -- http-cache TTL --------------------------------------------------------------


def test_cached_response_within_ttl_is_a_hit(mem_cache) -> None:
    mem_cache.put_cached_response("u://1", "body", "2026-06-20")
    assert mem_cache.get_cached_response("u://1", ttl_days=30, now="2026-07-09") == "body"


def test_cached_response_past_ttl_is_a_miss(mem_cache) -> None:
    mem_cache.put_cached_response("u://1", "body", "2026-01-01")
    assert mem_cache.get_cached_response("u://1", ttl_days=30, now="2026-07-09") is None


def test_no_ttl_preserves_never_expire_behaviour(mem_cache) -> None:
    mem_cache.put_cached_response("u://1", "body", "1999-01-01")
    assert mem_cache.get_cached_response("u://1") == "body"


def test_unparseable_lineage_is_treated_as_stale(mem_cache) -> None:
    mem_cache.put_cached_response("u://1", "body", "not-a-date")
    assert mem_cache.get_cached_response("u://1", ttl_days=3650, now="2026-07-09") is None


def test_expire_http_cache_deletes_only_stale_rows(mem_cache) -> None:
    mem_cache.put_cached_response("u://old", "old", "2026-01-01")
    mem_cache.put_cached_response("u://new", "new", "2026-07-01")
    removed = mem_cache.expire_http_cache(ttl_days=30, now="2026-07-09")
    assert removed == 1
    assert mem_cache.get_cached_response("u://old") is None
    assert mem_cache.get_cached_response("u://new") == "new"


# -- refresh_catalog (the correction path) ---------------------------------------


def test_refresh_reports_identity_label_changes_with_before_and_after(mem_cache) -> None:
    old = make_artist("corrected", gender=Gender.UNKNOWN)
    new = make_artist("corrected", gender=Gender.WOMAN)
    mem_cache.put_artist(old, fetched_at="2026-06-01")
    changes = refresh_catalog(mem_cache, {"corrected": new}, fetched_at="2026-07-09")
    assert len(changes) == 1
    assert changes[0].artist_id == "corrected"
    assert changes[0].old.gender is Gender.UNKNOWN
    assert changes[0].new.gender is Gender.WOMAN
    # and the corrected label is what the cache now holds:
    stored = mem_cache.get_artist("corrected")
    assert stored is not None and stored.identity.gender is Gender.WOMAN


def test_refresh_is_silent_for_unchanged_and_new_artists(mem_cache) -> None:
    unchanged = make_artist("same", gender=Gender.NONBINARY)
    mem_cache.put_artist(unchanged, fetched_at="2026-06-01")
    brand_new = make_artist("new-artist", gender=Gender.UNKNOWN)
    changes = refresh_catalog(
        mem_cache, {"same": unchanged, "new-artist": brand_new}, fetched_at="2026-07-09"
    )
    assert changes == []  # nothing changed, nothing invented
    assert mem_cache.get_artist("new-artist") is not None  # but the new artist is stored


# -- `lavender refresh` CLI -------------------------------------------------------------


def test_cli_refresh_populates_a_fresh_cache_and_exits_zero(tmp_path, capsys) -> None:
    db = tmp_path / "cache.db"
    assert cli_main(["refresh", "--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "DEMO ONLY" in out
    assert "no upstream identity API was queried" in out
    assert "no identity-label changes" in out
    assert "expired 0 stale http-cache row(s)" in out
    with Cache(db) as cache:
        assert cache.get_artist("mitski") is not None  # demo catalog was persisted


def test_cli_refresh_unknown_artist_fails(tmp_path, capsys) -> None:
    assert cli_main(["refresh", "--db", str(tmp_path / "c.db"), "--artist", "nope"]) == 1
    assert "no such artist" in capsys.readouterr().err


def test_cli_refresh_rejects_negative_ttl(tmp_path) -> None:
    with pytest.raises(SystemExit) as exc:
        cli_main(["refresh", "--db", str(tmp_path / "c.db"), "--ttl-days", "-1"])
    assert exc.value.code == 2


# -- #70: `lavender refresh` must not delete a filed correction ------------------------


def _seed_stale_only_in_retrieved_at(db, artist_id: str) -> str:
    """Cache a copy of a demo artist differing from the fixture ONLY in the date.

    Returns the citation the seeded source carries. A refresh against the fixture
    then produces an ``IdentityLabelChange`` whose ``old_value`` and ``new_value``
    are byte-identical — the exact shape that used to reconcile a pending
    correction away.
    """
    from dataclasses import replace

    from pipeline.demo import demo_catalog

    artist = demo_catalog()[artist_id]
    stale_sources = tuple(replace(s, retrieved_at="2020-01-01") for s in artist.identity.sources)
    stale = replace(artist, identity=replace(artist.identity, sources=stale_sources))
    with Cache(db) as cache:
        cache.put_artist(stale, fetched_at="2020-01-01")
    return artist.identity.sources[0].citation


def test_cli_refresh_does_not_delete_a_pending_correction(tmp_path, capsys) -> None:
    """THE #70 regression, end to end through the CLI.

    A person files a note saying an editorial database has their gender wrong.
    An ordinary refresh — which queries no upstream, and in which the asserted
    value does not move at all — used to delete that note and report
    "reconciled 1 pending upstream correction(s)".
    """
    from pipeline import corrections

    db = tmp_path / "cache.db"
    pending = tmp_path / "pending.json"
    citation = _seed_stale_only_in_retrieved_at(db, "snail-mail")
    corrections.add_correction(
        pending,
        artist_id="snail-mail",
        source_kind="musicbrainz-gender",
        citation=citation,
        current_value="female",
        proposed_value="nonbinary",
        note="the editorial record is wrong",
        filed_at="2026-08-06",
    )

    exit_code = cli_main(
        [
            "refresh",
            "--artist",
            "snail-mail",
            "--db",
            str(db),
            "--pending-corrections",
            str(pending),
        ]
    )
    assert exit_code == 0

    rows = corrections.list_corrections(pending)
    assert len(rows) == 1, "an ordinary refresh deleted a filed correction"
    assert rows[0].proposed_value == "nonbinary"
    out = capsys.readouterr().out
    # The asserted value moved for nobody, and nothing upstream was consulted —
    # the report must not claim otherwise.
    assert "reconciled 0" in out
    assert "no upstream identity source was queried" in out
    assert "1 pending correction(s) still open" in out
    assert "reconciled 1 pending upstream correction(s)" not in out
