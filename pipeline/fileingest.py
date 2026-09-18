"""Read a listening history out of a file the listener already has.

Until now the only route to a real profile was a live Last.fm key, so someone
who left Last.fm, listens on Spotify, or simply will not create a developer
account could not run this tool on their own history at all. A file import is
also the most local-first ingest available: the listens never leave the machine,
not even to the service they came from.

**Four shapes, each checked strictly.** ``auto`` sniffs; every other value pins
one parser. What each parser requires is documented per parser below, and that
documentation is a claim about *this code*, not a promise about what a vendor's
export looks like this month — a file that does not match fails naming the
column or key it wanted, rather than importing a plausible-looking subset.

**A skipped row is counted and reported, never silently dropped.** That is the
whole reason :class:`ImportResult` carries ``rows_skipped`` and a reason
breakdown rather than just a list of scrobbles: an importer that quietly keeps
what it understood turns a half-read file into a listening history that looks
complete, which is this project's dominant defect class wearing a new hat.

**Identity is untouched here.** This module produces plays. Nothing in it reads,
guesses, or writes an identity: the artist key is the file's MBID when it
carries one and the artist's name otherwise — the same rule
:func:`pipeline.lastfm.parse_recent_tracks` applies — and an artist that cannot
be resolved to a permitted source stays first-class ``UNKNOWN``.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from pipeline.lastfm import parse_recent_tracks
from pipeline.models import Scrobble

#: Every format this module can read. ``auto`` picks one by sniffing.
FORMATS: tuple[str, ...] = (
    "auto",
    "lastfm-csv",
    "lastfm-json",
    "spotify",
    "listenbrainz",
    "csv",
)

#: Columns each CSV parser requires, in the order they are reported missing.
REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "lastfm-csv": ("artist", "track", "uts"),
    "csv": ("artist", "track", "timestamp"),
}


class FileIngestError(ValueError):
    """The file is not the shape this format requires.

    Distinct from a skipped row on purpose. A missing column is a statement
    about the whole file — every row is unreadable and importing zero of them
    while reporting success would be the silent-partial-read failure this module
    exists to refuse. A malformed *row* is survivable and is counted instead.
    """


@dataclass(frozen=True)
class ImportResult:
    """What one file actually yielded — including what it did not."""

    fmt: str
    scrobbles: tuple[Scrobble, ...]
    rows_read: int
    skipped: Mapping[str, int] = field(default_factory=dict)

    @property
    def rows_skipped(self) -> int:
        return sum(self.skipped.values())

    def summary_line(self) -> str:
        """One honest sentence. Never reports a clean read of a partly-read file."""
        bits = [f"read {self.rows_read} row(s) as {self.fmt}", f"{len(self.scrobbles)} play(s)"]
        if self.rows_skipped:
            reasons = ", ".join(
                f"{count} {reason}" for reason, count in sorted(self.skipped.items())
            )
            bits.append(f"{self.rows_skipped} rows skipped ({reasons})")
        return "; ".join(bits)


def _timestamp(value: object) -> Optional[int]:
    """Unix seconds from a unix-seconds or ISO-8601 value, or ``None``.

    Returning ``None`` rather than a default is deliberate: a play with no
    readable time cannot be deduplicated and cannot be placed in an era window,
    and substituting "now" would invent listening that did not happen.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _rows_to_scrobbles(rows: Iterable[Mapping[str, object]], *, fmt: str) -> ImportResult:
    scrobbles: list[Scrobble] = []
    skipped: dict[str, int] = {}
    read = 0
    for row in rows:
        read += 1
        name = str(row.get("artist") or "").strip()
        title = str(row.get("track") or "").strip()
        when = _timestamp(row.get("ts"))
        if not name:
            skipped["missing artist"] = skipped.get("missing artist", 0) + 1
            continue
        if not title:
            skipped["missing track"] = skipped.get("missing track", 0) + 1
            continue
        if when is None:
            skipped["unreadable timestamp"] = skipped.get("unreadable timestamp", 0) + 1
            continue
        mbid = str(row.get("mbid") or "").strip()
        scrobbles.append(Scrobble(artist_id=mbid or name, artist_name=name, track=title, ts=when))
    return ImportResult(fmt=fmt, scrobbles=tuple(scrobbles), rows_read=read, skipped=skipped)


def _read_csv(path: Path, *, fmt: str) -> ImportResult:
    required = REQUIRED_COLUMNS[fmt]
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = [name.strip().lower() for name in (reader.fieldnames or [])]
        for column in required:
            if column not in header:
                raise FileIngestError(
                    f"{path.name}: {fmt} needs a {column!r} column; this file has "
                    f"{header or 'no header row'}"
                )
        ts_column = required[2]
        rows = [
            {
                "artist": row.get("artist"),
                "track": row.get("track"),
                "ts": row.get(ts_column),
                "mbid": row.get("artist_mbid") or row.get("mbid"),
            }
            for row in ({(k or "").strip().lower(): v for k, v in raw.items()} for raw in reader)
        ]
    return _rows_to_scrobbles(rows, fmt=fmt)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FileIngestError(
            f"{path.name}: not valid JSON ({exc.msg} at line {exc.lineno})"
        ) from exc


def _read_lastfm_json(path: Path) -> ImportResult:
    """Last.fm's ``user.getrecenttracks`` shape — one page, or a list of pages."""
    payload = _read_json(path)
    pages = payload if isinstance(payload, list) else [payload]
    scrobbles: list[Scrobble] = []
    read = 0
    for page in pages:
        if not isinstance(page, dict) or "recenttracks" not in page:
            raise FileIngestError(
                f"{path.name}: lastfm-json needs a 'recenttracks' key "
                "(the shape user.getrecenttracks returns)"
            )
        tracks = page.get("recenttracks", {})
        entries = tracks.get("track", []) if isinstance(tracks, dict) else []
        read += len(entries) if isinstance(entries, list) else 1
        scrobbles.extend(parse_recent_tracks(page))
    skipped = read - len(scrobbles)
    return ImportResult(
        fmt="lastfm-json",
        scrobbles=tuple(scrobbles),
        rows_read=read,
        # `parse_recent_tracks` skips now-playing entries and malformed rows
        # without distinguishing them. Reporting one honest bucket is better
        # than inventing a breakdown it did not produce.
        skipped={"not a completed play, or malformed": skipped} if skipped > 0 else {},
    )


def _read_spotify(path: Path) -> ImportResult:
    """Spotify "Extended streaming history": a list of streaming events."""
    payload = _read_json(path)
    if not isinstance(payload, list):
        raise FileIngestError(f"{path.name}: spotify export must be a JSON array of plays")
    rows: list[dict[str, object]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            rows.append({})
            continue
        if "master_metadata_album_artist_name" not in entry:
            raise FileIngestError(
                f"{path.name}: spotify export needs a "
                "'master_metadata_album_artist_name' key on every play"
            )
        rows.append(
            {
                "artist": entry.get("master_metadata_album_artist_name"),
                "track": entry.get("master_metadata_track_name"),
                "ts": entry.get("ts"),
            }
        )
    return _rows_to_scrobbles(rows, fmt="spotify")


def _artist_mbid(metadata: Mapping[str, object]) -> str:
    """The first artist MBID a ListenBrainz listen carries, if any."""
    for holder in ("mbid_mapping", "additional_info"):
        block = metadata.get(holder)
        if isinstance(block, dict):
            mbids = block.get("artist_mbids")
            if isinstance(mbids, list) and mbids:
                return str(mbids[0]).strip()
    return ""


def _read_listenbrainz(path: Path) -> ImportResult:
    """ListenBrainz export: listens with ``track_metadata`` and, often, MBIDs."""
    payload = _read_json(path)
    listens = payload.get("listens") if isinstance(payload, dict) else payload
    if not isinstance(listens, list):
        raise FileIngestError(
            f"{path.name}: listenbrainz export must be a JSON array of listens, "
            "or an object with a 'listens' array"
        )
    rows: list[dict[str, object]] = []
    for entry in listens:
        if not isinstance(entry, dict):
            rows.append({})
            continue
        if "track_metadata" not in entry:
            raise FileIngestError(
                f"{path.name}: listenbrainz export needs a 'track_metadata' key on every listen"
            )
        metadata = entry.get("track_metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        rows.append(
            {
                "artist": metadata.get("artist_name"),
                "track": metadata.get("track_name"),
                "ts": entry.get("listened_at"),
                "mbid": _artist_mbid(metadata),
            }
        )
    return _rows_to_scrobbles(rows, fmt="listenbrainz")


def detect_format(path: Path) -> str:
    """Sniff one of the concrete formats, or say why it cannot.

    Never falls back to a default. A wrong guess would import a file under the
    wrong contract and skip every row it did not understand — reporting a
    successful import of almost nothing.
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace").lstrip()
    if text.startswith("{") or text.startswith("["):
        payload = _read_json(path)
        if isinstance(payload, dict) and "recenttracks" in payload:
            return "lastfm-json"
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            first = payload[0]
            if "master_metadata_album_artist_name" in first:
                return "spotify"
            if "track_metadata" in first:
                return "listenbrainz"
            if "recenttracks" in first:
                return "lastfm-json"
        if isinstance(payload, dict) and isinstance(payload.get("listens"), list):
            return "listenbrainz"
        raise FileIngestError(
            f"{path.name}: JSON, but not a shape this reads. Pass --format to say which of "
            f"{', '.join(f for f in FORMATS if f != 'auto')} it is."
        )
    header = text.splitlines()[0] if text.splitlines() else ""
    columns = {name.strip().lower() for name in header.split(",")}
    for fmt in ("lastfm-csv", "csv"):
        if set(REQUIRED_COLUMNS[fmt]).issubset(columns):
            return fmt
    raise FileIngestError(
        f"{path.name}: no format matched. lastfm-csv needs "
        f"{list(REQUIRED_COLUMNS['lastfm-csv'])}, csv needs {list(REQUIRED_COLUMNS['csv'])}; "
        f"this file's header is {sorted(columns) if header else 'empty'}."
    )


def read_history(path: str | Path, *, fmt: str = "auto") -> ImportResult:
    """Read one exported listening history. No network, no identity, no writes."""
    if fmt not in FORMATS:
        raise FileIngestError(f"unknown format {fmt!r}; choose one of {', '.join(FORMATS)}")
    source = Path(path)
    if not source.is_file():
        raise FileIngestError(f"{source}: no such file")
    resolved = detect_format(source) if fmt == "auto" else fmt
    if resolved in REQUIRED_COLUMNS:
        return _read_csv(source, fmt=resolved)
    if resolved == "lastfm-json":
        return _read_lastfm_json(source)
    if resolved == "spotify":
        return _read_spotify(source)
    return _read_listenbrainz(source)


def dedupe(scrobbles: Sequence[Scrobble]) -> tuple[Scrobble, ...]:
    """Drop exact repeats, order-preserving — the cache's own dedupe key.

    The cache already enforces ``UNIQUE(username, artist_id, track, ts)``, so
    this changes no stored row. It exists so the count this command *reports*
    matches what a re-import will actually add, instead of promising rows the
    database will silently ignore.
    """
    seen: set[tuple[str, str, int]] = set()
    unique: list[Scrobble] = []
    for scrobble in scrobbles:
        key = (scrobble.artist_id, scrobble.track, scrobble.ts)
        if key in seen:
            continue
        seen.add(key)
        unique.append(scrobble)
    return tuple(unique)


class ImportedHistory:
    """A :class:`~pipeline.lastfm.ScrobbleSource` over an imported file.

    Plays, and nothing else. An export carries what someone listened to and
    when; it does not carry Last.fm's tag vocabulary or its similar-artist
    graph, and this refuses to pretend otherwise — :meth:`artist_tags` and
    :meth:`similar_artists` return empty rather than a guess.

    That is a real limitation and it is stated at both surfaces that meet it:
    identity still resolves from the permitted sources under ``--enrich``, but a
    file-only world has no content or collaborative signal to rank with until a
    Last.fm sync supplies one. Filling either from track names would be
    inference, which is the one thing this project does not do.
    """

    def __init__(self, username: str, scrobbles: Sequence[Scrobble]) -> None:
        self._username = username
        self._scrobbles = list(scrobbles)

    def recent_scrobbles(self, username: str, limit: int = 200) -> list[Scrobble]:
        if username != self._username:
            return []
        return self._scrobbles[:limit]

    def scrobbles_since(
        self, username: str, since_ts: int = 0, page_size: int = 200
    ) -> list[Scrobble]:
        if username != self._username:
            return []
        ordered = sorted(self._scrobbles, key=lambda s: s.ts)
        return [s for s in ordered if s.ts > since_ts]

    def artist_tags(self, artist_id: str) -> tuple[str, ...]:
        return ()

    def similar_artists(self, artist_id: str) -> list[tuple[str, float]]:
        return []
