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
from typing import Any, Optional

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
    """One upstream edit worth making, with whatever is already cited for it."""

    category: str
    name: str
    mbid: str
    plays: int
    detail: str = ""
    #: ``(source kind, citation)`` for every claim already on record about this
    #: artist. Shown so the reader can see what upstream *does* say before
    #: changing it — a field that looks empty here may be empty because nobody
    #: sourced it, or because the claim lives on a different axis.
    citations: tuple[tuple[str, str], ...] = ()
    #: ``(label, url)`` external links to *look* in — never citations themselves.
    links: tuple[tuple[str, str], ...] = ()

    @property
    def edit_url(self) -> str:
        return MB_EDIT.format(mbid=self.mbid)

    @property
    def needs_citation(self) -> bool:
        """Whether making this edit means recording a claim about a person."""
        return self.category in _IDENTITY_CATEGORIES


#: Categories whose edit records something about a person, and therefore may
#: only be made from a public self-identification you can cite.
_IDENTITY_CATEGORIES = frozenset({"person-gender", "front-person-gender"})

#: MusicBrainz URL-relation types worth following when looking for a public
#: self-identification, most-likely-to-be-the-artist's-own-words first. An
#: official site or the artist's own social account is a primary source; a
#: database entry is somebody else's summary, useful for finding the primary one.
#: Deliberately excludes streaming, purchase, lyrics and setlist links, which
#: say nothing about a person and would bury the useful ones.
_RESEARCH_RELATIONS: tuple[str, ...] = (
    "official homepage",
    "social network",
    "bandcamp",
    "wikidata",
    "wikipedia",
    "discogs",
    "allmusic",
)
#: How many to show. The point is a place to start, not an exhaustive index.
_MAX_RESEARCH_LINKS = 5


def research_links(record: Optional[dict[str, object]]) -> tuple[tuple[str, str], ...]:
    """Where to go looking for a citable self-identification, from MusicBrainz.

    These are not citations and are never presented as such — they are the
    external links MusicBrainz already holds for the artist, ordered so the
    artist's own words come first. The document previously showed "nothing cited
    yet" and stopped there, which is true but useless: every one of these
    artists is unknown precisely *because* nothing is on record, so a worklist
    that only lists what is already cited has nothing to say about exactly the
    rows that need work.

    Free, in request terms: the enricher already fetches ``inc=url-rels`` for
    the P21 link, so these were being parsed and thrown away.
    """
    if not isinstance(record, dict):
        return ()
    found: dict[str, str] = {}
    for wanted in _RESEARCH_RELATIONS:
        for relation in record.get("relations") or []:
            if not isinstance(relation, dict) or str(relation.get("type", "")) != wanted:
                continue
            target = relation.get("url")
            resource = str(target.get("resource", "")).strip() if isinstance(target, dict) else ""
            if resource and wanted not in found:
                found[wanted] = resource
    return tuple(found.items())[:_MAX_RESEARCH_LINKS]


def existing_citations(artist: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    """Every citation already on record for this artist, deduplicated.

    Reads all three places a claim can live — the individual's gender, the
    second axis (ADR 0011), and the band's sourced lineup, including each
    front-person's own label — because "what is already cited here" is the
    question a person about to edit upstream actually needs answered.
    """
    found: list[tuple[str, str]] = []

    def take(container: object) -> None:
        if not isinstance(container, dict):
            return
        for key in ("sources", "orientation_sources", "trans_sources"):
            for source in container.get(key) or []:
                if isinstance(source, dict) and source.get("citation"):
                    found.append((str(source.get("kind", "?")), str(source["citation"])))

    take(artist.get("identity"))
    take(artist.get("queer"))
    composition = artist.get("composition")
    if isinstance(composition, dict):
        take(composition)
        for person in composition.get("members_fronting") or []:
            if isinstance(person, dict):
                take(person.get("identity"))
    return tuple(dict.fromkeys(found))


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


def classify(artist: dict[str, object], upstream: CachedUpstream, plays: int) -> list[Item]:
    """The edits that would move this artist from unknown to sourced.

    A list rather than one item, because a band with three unsourced
    front-people is three edits on three different pages — see
    :func:`_front_person_items`.
    """
    name = str(artist.get("name", ""))
    identity = artist.get("identity") or {}
    mbid = upstream.mbid_for(str(artist.get("artist_id", "")))
    if mbid is None:
        return []  # unresolvable upstream; a local MBID pin, not an edit, is the fix
    record = upstream.document(musicbrainz_lookup_url(mbid))
    if record is None:
        return []

    composition = artist.get("composition")
    if isinstance(composition, dict) and composition.get("members_fronting"):
        return _front_person_items(composition, record, upstream, name, plays)

    if isinstance(identity, dict) and identity.get("gender", "unknown") != "unknown":
        return []

    citations = existing_citations(artist)
    links = research_links(record)
    if str(record.get("type", "")).lower() != "group":
        if record.get("gender") is None:
            return [Item("person-gender", name, mbid, plays, "", citations, links)]
        return []
    return _classify_group(record, name, mbid, plays, citations, links)


def _front_person_items(
    composition: dict[str, object],
    record: dict[str, object],
    upstream: CachedUpstream,
    band: str,
    plays: int,
) -> list[Item]:
    """One item per front-person whose own gender is empty — on *their* page.

    The gender field for a band's singer lives on the singer's MusicBrainz
    entity, not the band's, so pointing this row at the band was an edit link to
    a page without the field on it. Their MBID is not in our cached
    ``FrontPerson`` (which stores name, role and label only), so it is recovered
    from the band's own member relations.
    """
    unsourced = {
        str(person.get("name", ""))
        for person in composition.get("members_fronting") or []
        if isinstance(person, dict)
        and (person.get("identity") or {}).get("gender", "unknown") == "unknown"
    }
    if not unsourced:
        return []
    items: list[Item] = []
    # A person can hold several member relations to the same band — one per
    # instrument, or per stint — and each would otherwise become a duplicate row
    # for a single edit.
    seen: set[str] = set()
    for relation in record.get("relations") or []:
        if not isinstance(relation, dict) or str(relation.get("type", "")) != "member of band":
            continue
        related = relation.get("artist")
        if not isinstance(related, dict):
            continue
        person_name = str(related.get("name", ""))
        person_mbid = str(related.get("id", "")).strip().lower()
        if person_name not in unsourced or not looks_like_mbid(person_mbid):
            continue
        if person_mbid in seen:
            continue
        seen.add(person_mbid)
        person_record = upstream.document(musicbrainz_lookup_url(person_mbid))
        items.append(
            Item(
                "front-person-gender",
                person_name,
                person_mbid,
                plays,
                f"fronts {band}",
                (),
                research_links(person_record),
            )
        )
    return items


def _classify_group(
    record: dict[str, object],
    name: str,
    mbid: str,
    plays: int,
    citations: tuple[tuple[str, str], ...] = (),
    links: tuple[tuple[str, str], ...] = (),
) -> list[Item]:
    """Which lineup edit a band needs: the members, or a role on one of them."""
    relations = [
        r
        for r in (record.get("relations") or [])
        if isinstance(r, dict) and str(r.get("type", "")).lower() == "member of band"
    ]
    if not relations:
        return [Item("no-lineup", name, mbid, plays, "", citations, links)]
    marked = any(is_fronting_role(str(a)) for r in relations for a in (r.get("attributes") or []))
    if not marked:
        detail = f"{len(relations)} member(s) listed"
        return [Item("fronting-role", name, mbid, plays, detail, citations, links)]
    return []


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
            items.extend(classify(artist, upstream, plays.get(str(artist.get("artist_id", "")), 0)))
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
        lines += [
            "| plays | artist | edit | already cited | where to look | notes |",
            "|---:|---|---|---|---|---|",
        ]
        for i in rows:
            if i.citations:
                cited = " ".join(f"[{kind}]({url})" for kind, url in i.citations)
            else:
                cited = "_nothing cited yet_" if i.needs_citation else "—"
            look = " ".join(f"[{label}]({url})" for label, url in i.links) or "—"
            lines.append(
                f"| {i.plays} | {i.name} | [edit]({i.edit_url}) | {cited} | {look} | {i.detail} |"
            )
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
  .cites {{ display: block; margin-top: .15rem; }}
  .cite {{ display: inline-block; margin-right: .4rem; font-size: .72rem;
    padding: .05rem .35rem; border: 1px solid var(--line); border-radius: 999px;
    text-decoration: none; }}
  .cite.none {{ color: var(--muted); border-style: dashed; }}
  .look {{ display: inline-block; margin-right: .4rem; font-size: .72rem;
    padding: .05rem .35rem; border-radius: 999px; text-decoration: none;
    color: var(--muted); background: color-mix(in srgb, var(--accent) 9%, transparent); }}
  .look:hover {{ color: var(--accent); }}
  .cap {{ grid-column: 3 / -1; display: flex; gap: .4rem; margin-top: .3rem; }}
  .cap:empty {{ display: none; }}
  .src {{ flex: 1 1 auto; font: inherit; font-size: .8rem; padding: .3rem .5rem;
    border: 1px solid var(--line); border-radius: 5px; background: var(--bg); color: var(--fg); }}
  .src.filled {{ border-color: var(--accent); }}
  .copy {{ font: inherit; font-size: .78rem; padding: .3rem .6rem; cursor: pointer;
    border: 1px solid var(--line); border-radius: 5px; background: var(--card); color: var(--fg); }}
  .copy:disabled {{ opacity: .4; cursor: not-allowed; }}
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
<p class="rule" style="border-left-color: var(--line)">Each row shows what is
<strong>already cited</strong> for that artist (bordered pills, linking to the source) and
<strong>where to look</strong> for one (filled pills — the external links MusicBrainz already
holds, artist's own words first). The second kind are starting points, never citations: an
official site or the artist's own account is a primary source, a database entry is someone
else's summary of one.</p>
<div class="tools">
  <input type="search" id="q" placeholder="Filter by artist name…"
    aria-label="Filter by artist name">
  <span class="count" id="count"></span>
</div>
{sections}
</main>
<script>
const KEY = "worklist:{user}";
const SRC = "worklist-citations:{user}";
const done = new Set(JSON.parse(localStorage.getItem(KEY) || "[]"));
const cites = JSON.parse(localStorage.getItem(SRC) || "{{}}");
const items = [...document.querySelectorAll("li")];
function save() {{ localStorage.setItem(KEY, JSON.stringify([...done])); }}
function count() {{
  const shown = items.filter(li => !li.classList.contains("hidden"));
  document.getElementById("count").textContent =
    `${{done.size}} done · ${{shown.length}} shown`;
}}
for (const li of items) {{
  const box = li.querySelector("input[type=checkbox]");
  if (done.has(li.dataset.id)) {{ box.checked = true; li.classList.add("done"); }}
  box.addEventListener("change", () => {{
    li.classList.toggle("done", box.checked);
    box.checked ? done.add(li.dataset.id) : done.delete(li.dataset.id);
    save(); count();
  }});

  const src = li.querySelector(".src");
  if (!src) continue;
  const copy = li.querySelector(".copy");
  const sync = () => {{
    const v = src.value.trim();
    src.classList.toggle("filled", !!v);
    copy.disabled = !v;
  }};
  src.value = cites[li.dataset.id] || "";
  sync();
  src.addEventListener("input", () => {{
    const v = src.value.trim();
    v ? (cites[li.dataset.id] = v) : delete cites[li.dataset.id];
    localStorage.setItem(SRC, JSON.stringify(cites));
    sync();
  }});
  copy.addEventListener("click", async () => {{
    // The edit note MusicBrainz asks for: the claim, and where it came from.
    const note = `Gender per the artist's own public self-identification: ${{src.value.trim()}}`;
    try {{ await navigator.clipboard.writeText(note); copy.textContent = "copied"; }}
    catch {{ copy.textContent = "copy failed"; }}
    setTimeout(() => (copy.textContent = "note"), 1400);
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
            cited = "".join(
                f'<a class="cite" href="{_escape(url)}" target="_blank" rel="noopener" '
                f'title="{_escape(url)}">{_escape(kind)}</a>'
                for kind, url in item.citations
            )
            if not cited:
                cited = (
                    '<span class="cite none">nothing cited yet</span>'
                    if item.needs_citation
                    else ""
                )
            cited += "".join(
                f'<a class="look" href="{_escape(url)}" target="_blank" rel="noopener" '
                f'title="{_escape(url)}">{_escape(label)}</a>'
                for label, url in item.links
            )
            # Only the edits that record something about a person get a capture
            # field. The rest are discography, where a citation is not the
            # gate — offering one everywhere would blur exactly the line this
            # document exists to hold.
            capture = (
                f'<input class="src" type="url" placeholder="paste the source you are citing…" '
                f'aria-label="Citation for {_escape(item.name)}">'
                f'<button class="copy" type="button" '
                f'title="Copy a MusicBrainz edit note">note</button>'
                if item.needs_citation
                else ""
            )
            lis.append(
                f'<li data-id="{_escape(item.mbid)}" data-name="{_escape(item.name.lower())}">'
                f'<input type="checkbox" aria-label="Done: {_escape(item.name)}">'
                f'<span class="plays">{item.plays}</span>'
                f'<span class="name">{_escape(item.name)} {note}'
                f'<span class="cites">{cited}</span></span>'
                f'<a href="{item.edit_url}" target="_blank" rel="noopener">edit &rarr;</a>'
                f'<div class="cap">{capture}</div></li>'
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
