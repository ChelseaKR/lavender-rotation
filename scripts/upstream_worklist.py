#!/usr/bin/env python3
"""What to fix upstream, ranked by how much it would actually change.

"Fix it at the source" (CONTRIBUTING.md) is the project's stated posture for a
wrong or missing identity claim, but until now it offered no way to see *what*
is missing or *which* gap is worth an evening. This turns a local cache into a
prioritised worklist of MusicBrainz edits, entirely offline — it re-reads the
cached upstream payloads, makes no request, and needs no credential.

The categories are deliberately kept apart, because they are different kinds of
work with very different care requirements:

* **fronting-role** — a band whose lineup MusicBrainz already knows, where no
  member carries a fronting role attribute. The fix is adding `lead vocals` (or
  `vocals`) to an existing "member of band" relation. This is ordinary
  discography work: a claim about who sings in a band, not a claim about anyone's
  identity, and no citation dilemma. It is also the largest category by far.
* **front-person-gender** — a band whose front-person *is* marked, but whose own
  gender field is empty. One edit, and the one that needs care: only where the
  artist has publicly self-identified and you can cite where.
* **no-lineup** — a band MusicBrainz has no members for at all. The most work
  per band, the least identity-sensitive.
* **person-gender** — a solo artist with an empty gender field. Same care rule
  as front-person-gender.

Artists are ranked by the operator's own play count, because a fix to a band
they play constantly improves the *candidate graph* — that band is a discovery
seed — and not merely one row's label.

Usage::

    python scripts/upstream_worklist.py --user <username> [--out worklist.md]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.enrich import (  # noqa: E402
    is_fronting_role,
    musicbrainz_lookup_url,
    musicbrainz_search_url,
    parse_musicbrainz_search,
)
from pipeline.lastfm import looks_like_mbid  # noqa: E402
from pipeline.paths import default_db_path  # noqa: E402

MB_ARTIST = "https://musicbrainz.org/artist/{mbid}"
MB_EDIT = "https://musicbrainz.org/artist/{mbid}/edit"

CATEGORIES = {
    "fronting-role": "Lineup is known; no member marked as fronting. Add `lead vocals`/`vocals` "
    "to an existing member relation — discography work, no identity claim.",
    "front-person-gender": "Front-person is marked; their own gender is empty. **Only edit where "
    "the artist has publicly self-identified and you can cite it.**",
    "no-lineup": "MusicBrainz has no members for this band. Adding the lineup is discography work.",
    "person-gender": "Solo artist with an empty gender field. **Same rule: self-identified and "
    "cited, or leave it empty.**",
}


@dataclass(frozen=True)
class Item:
    """One upstream edit worth making."""

    category: str
    name: str
    mbid: str
    plays: int
    detail: str = ""

    @property
    def edit_url(self) -> str:
        return MB_EDIT.format(mbid=self.mbid)


class CachedUpstream:
    """Read-only view of the MusicBrainz payloads a previous ingest stored."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._bodies = dict(
            conn.execute("SELECT url, body FROM http_cache WHERE url LIKE '%musicbrainz%'")
        )

    def document(self, url: str) -> Optional[dict[str, object]]:
        body = self._bodies.get(url)
        if body is None:
            return None
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def mbid_for(self, artist_id: str) -> Optional[str]:
        """Resolve exactly as the enricher did, from cache only."""
        if looks_like_mbid(artist_id):
            return artist_id.strip().lower()
        found = self.document(musicbrainz_search_url(artist_id))
        if found is None:
            return None
        try:
            return parse_musicbrainz_search(found, artist_id)
        except ValueError:
            return None


def classify(artist: dict[str, object], upstream: CachedUpstream, plays: int) -> Optional[Item]:
    """The one edit that would move this artist from unknown to sourced, if any."""
    name = str(artist.get("name", ""))
    identity = artist.get("identity") or {}
    composition = artist.get("composition")

    if isinstance(composition, dict) and composition.get("members_fronting"):
        fronts = [f for f in composition["members_fronting"] if isinstance(f, dict)]
        unsourced = [
            f for f in fronts if (f.get("identity") or {}).get("gender", "unknown") == "unknown"
        ]
        if fronts and len(unsourced) == len(fronts):
            mbid = upstream.mbid_for(str(artist.get("artist_id", "")))
            if mbid:
                who = ", ".join(str(f.get("name", "?")) for f in unsourced[:3])
                return Item("front-person-gender", name, mbid, plays, f"front: {who}")
        return None

    if isinstance(identity, dict) and identity.get("gender", "unknown") != "unknown":
        return None

    mbid = upstream.mbid_for(str(artist.get("artist_id", "")))
    if mbid is None:
        return None  # unresolvable upstream; a local MBID pin, not an edit, is the fix
    record = upstream.document(musicbrainz_lookup_url(mbid))
    if record is None:
        return None

    if str(record.get("type", "")).lower() != "group":
        if record.get("gender") is None:
            return Item("person-gender", name, mbid, plays)
        return None
    return _classify_group(record, name, mbid, plays)


def _classify_group(record: dict[str, object], name: str, mbid: str, plays: int) -> Optional[Item]:
    """Which lineup edit a band needs: the members, or a role on one of them."""
    relations = [
        r
        for r in (record.get("relations") or [])
        if isinstance(r, dict) and str(r.get("type", "")).lower() == "member of band"
    ]
    if not relations:
        return Item("no-lineup", name, mbid, plays)
    marked = any(is_fronting_role(str(a)) for r in relations for a in (r.get("attributes") or []))
    if not marked:
        return Item("fronting-role", name, mbid, plays, f"{len(relations)} member(s) listed")
    return None


def collect(db_path: Path, username: str) -> list[Item]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=60)
    try:
        plays = dict(
            conn.execute(
                "SELECT artist_id, COUNT(*) FROM scrobbles WHERE username = ? GROUP BY artist_id",
                (username,),
            )
        )
        upstream = CachedUpstream(conn)
        items: list[Item] = []
        for (payload,) in conn.execute("SELECT payload FROM artists"):
            artist = json.loads(payload)
            item = classify(artist, upstream, plays.get(str(artist.get("artist_id", "")), 0))
            if item is not None:
                items.append(item)
    finally:
        conn.close()
    return sorted(items, key=lambda i: (-i.plays, i.name))


def render(items: list[Item], username: str) -> str:
    by_category: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        by_category[item.category].append(item)

    lines = [
        "# Upstream worklist",
        "",
        f"Generated from the local cache for `{username}` — offline, no requests made.",
        "Regenerate with `python scripts/upstream_worklist.py --user <username>`.",
        "",
        "**The rule for anything touching a person's gender:** only edit where the artist has",
        "publicly self-identified and you can cite where. Not from a photo, a name, a voice, or",
        "press pronouns. That is this project's no-inference guardrail pointed outward — and an",
        "unsourced edit upstream is worse than one here, because it publishes the guess to",
        "everyone. If you cannot cite it, an empty field is the correct state.",
        "",
        f"**{len(items)} artists** across {len(by_category)} categories.",
        "",
    ]
    for category, blurb in CATEGORIES.items():
        rows = by_category.get(category, [])
        if not rows:
            continue
        lines += [f"## {category} ({len(rows)})", "", blurb, ""]
        lines += ["| plays | artist | edit | notes |", "|---:|---|---|---|"]
        lines += [f"| {i.plays} | {i.name} | [edit]({i.edit_url}) | {i.detail} |" for i in rows]
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="username whose cache to read")
    parser.add_argument("--db", default=None, help="cache path (default: the resolved data dir)")
    parser.add_argument("--out", default="upstream-worklist.md")
    args = parser.parse_args(argv)

    db_path = Path(args.db) if args.db else default_db_path()
    items = collect(db_path, args.user)
    Path(args.out).write_text(render(items, args.user), encoding="utf-8")
    counts = defaultdict(int)
    for item in items:
        counts[item.category] += 1
    print(f"wrote {args.out}: {len(items)} artists")  # noqa: T201
    for category in CATEGORIES:
        if counts[category]:
            print(f"  {counts[category]:>5}  {category}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
