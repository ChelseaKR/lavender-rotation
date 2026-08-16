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


#: Self-contained on purpose. This document carries a personal listening history
#: (play counts) alongside identity data, so it stays a local file — no CDN, no
#: web font, no analytics, nothing that would make opening it a network event.
_HTML_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Upstream worklist — {user}</title>
<style>
  :root {{ color-scheme: light dark;
    --bg: #fdfcff; --fg: #1a1725; --muted: #5d5670; --line: #e2dced;
    --card: #ffffff; --accent: #6b4d8f; --done: #8b85a0; }}
  @media (prefers-color-scheme: dark) {{ :root {{
    --bg: #16131c; --fg: #ece8f2; --muted: #a79fb8; --line: #2e2839;
    --card: #1e1a26; --accent: #c3a8e0; --done: #635c74; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 2rem 1.25rem 5rem; background: var(--bg); color: var(--fg);
    font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 60rem; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 .35rem; letter-spacing: -.01em; }}
  .sub {{ color: var(--muted); margin: 0 0 1.5rem; font-size: .92rem; }}
  .rule {{ background: var(--card); border: 1px solid var(--line);
    border-left: 3px solid var(--accent); border-radius: 6px;
    padding: .85rem 1rem; margin: 0 0 1.75rem; font-size: .9rem; }}
  .rule strong {{ color: var(--accent); }}
  .tools {{ position: sticky; top: 0; background: var(--bg); padding: .6rem 0 .75rem;
    border-bottom: 1px solid var(--line); margin-bottom: 1.5rem; display: flex; gap: .75rem;
    align-items: center; flex-wrap: wrap; z-index: 2; }}
  input[type=search] {{ flex: 1 1 14rem; padding: .5rem .7rem; font: inherit; font-size: .92rem;
    border: 1px solid var(--line); border-radius: 6px; background: var(--card); color: var(--fg); }}
  .count {{ color: var(--muted); font-size: .88rem; font-variant-numeric: tabular-nums; }}
  section {{ margin: 0 0 2.5rem; }}
  h2 {{ font-size: 1.05rem; margin: 0 0 .3rem; }}
  h2 .n {{ color: var(--muted); font-weight: 400; }}
  .why {{ color: var(--muted); font-size: .88rem; margin: 0 0 .9rem; }}
  ul {{ list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }}
  li {{ display: grid; grid-template-columns: 1.6rem 4.5rem 1fr auto; gap: .75rem;
    align-items: baseline; padding: .5rem .3rem;
    border-bottom: 1px solid var(--line); }}
  li.done {{ opacity: .45; }}
  li.done .name {{ text-decoration: line-through; }}
  li.hidden {{ display: none; }}
  .plays {{ color: var(--muted); font-size: .82rem; text-align: right;
    font-variant-numeric: tabular-nums; }}
  .name {{ font-weight: 500; }}
  .note {{ color: var(--muted); font-size: .8rem; font-weight: 400; }}
  a {{ color: var(--accent); font-size: .85rem; }}
  input[type=checkbox] {{ width: 1.05rem; height: 1.05rem; accent-color: var(--accent); }}
</style></head><body><main>
<h1>Upstream worklist</h1>
<p class="sub">{total} artists · generated from the local cache for <strong>{user}</strong> ·
offline, no requests made · regenerate with
<code>python scripts/upstream_worklist.py --user {user} --format html</code></p>
<p class="rule"><strong>Before editing anything that touches a person's gender:</strong> only where
the artist has publicly self-identified and you can cite where. Not from a photo, a name, a voice,
or press pronouns. That is this project's no-inference guardrail pointed outward — and an unsourced
edit upstream is worse than one here, because it publishes the guess to everyone. If you cannot
cite it, an empty field is the correct state.</p>
<div class="tools">
  <input type="search" id="q" placeholder="Filter by artist name…"
    aria-label="Filter by artist name">
  <span class="count" id="count"></span>
</div>
{sections}
</main>
<script>
const KEY = "worklist:{user}";
const done = new Set(JSON.parse(localStorage.getItem(KEY) || "[]"));
const items = [...document.querySelectorAll("li")];
function save() {{ localStorage.setItem(KEY, JSON.stringify([...done])); }}
function count() {{
  const shown = items.filter(li => !li.classList.contains("hidden"));
  document.getElementById("count").textContent =
    `${{done.size}} done · ${{shown.length}} shown`;
}}
for (const li of items) {{
  const box = li.querySelector("input");
  if (done.has(li.dataset.id)) {{ box.checked = true; li.classList.add("done"); }}
  box.addEventListener("change", () => {{
    li.classList.toggle("done", box.checked);
    box.checked ? done.add(li.dataset.id) : done.delete(li.dataset.id);
    save(); count();
  }});
}}
document.getElementById("q").addEventListener("input", e => {{
  const term = e.target.value.trim().toLowerCase();
  for (const li of items)
    li.classList.toggle("hidden", term && !li.dataset.name.includes(term));
  count();
}});
count();
</script></body></html>
"""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def render_html(items: list[Item], username: str) -> str:
    """A working document: tickable, filterable, and entirely local."""
    by_category: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        by_category[item.category].append(item)

    sections = []
    for category, blurb in CATEGORIES.items():
        rows = by_category.get(category, [])
        if not rows:
            continue
        lis = []
        for item in rows:
            note = f'<span class="note">{_escape(item.detail)}</span>' if item.detail else ""
            lis.append(
                f'<li data-id="{_escape(item.mbid)}" data-name="{_escape(item.name.lower())}">'
                f'<input type="checkbox" aria-label="Done: {_escape(item.name)}">'
                f'<span class="plays">{item.plays}</span>'
                f'<span class="name">{_escape(item.name)} {note}</span>'
                f'<a href="{item.edit_url}" target="_blank" rel="noopener">edit &rarr;</a></li>'
            )
        blurb_html = _escape(blurb).replace("**", "")
        sections.append(
            f'<section><h2>{category} <span class="n">({len(rows)})</span></h2>'
            f'<p class="why">{blurb_html}</p><ul>{"".join(lis)}</ul></section>'
        )
    return _HTML_SHELL.format(user=_escape(username), total=len(items), sections="".join(sections))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="username whose cache to read")
    parser.add_argument("--db", default=None, help="cache path (default: the resolved data dir)")
    parser.add_argument("--out", default="upstream-worklist.md")
    parser.add_argument(
        "--format",
        choices=("md", "html"),
        default="md",
        help="html produces a self-contained working document: tickable, filterable, "
        "progress kept in the browser's own storage. No network, no hosting.",
    )
    args = parser.parse_args(argv)

    db_path = Path(args.db) if args.db else default_db_path()
    items = collect(db_path, args.user)
    renderer = render_html if args.format == "html" else render
    Path(args.out).write_text(renderer(items, args.user), encoding="utf-8")
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
