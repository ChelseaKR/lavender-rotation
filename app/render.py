"""Render recommendations to accessible, semantic HTML.

This pure renderer is the single source of truth for the *content* of a why-card.
The Streamlit dashboard shows the same information interactively; this static
output is what the a11y gate (:mod:`app.a11y_check` / pa11y) audits, so the
mechanical WCAG 2.2 AA checks run in CI without a live browser server.

Accessibility decisions baked in here:

* every page has ``lang`` + a viewport meta (zoom/reflow at 320 px),
* data tables sit in keyboard-focusable scroll regions so the page itself never
  scrolls horizontally at 320 px (reflow, verified by the Playwright specs in
  ``tests/test_e2e_a11y.py``),
* a skip link to ``<main>`` and proper landmarks/heading order,
* identity is conveyed as **text**, never colour alone,
* the score "chart" ships with a real ``<table>`` data equivalent,
* upstream correction links are labelled text links, never icon/colour cues.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape
from typing import cast

from pipeline.models import Recommendation
from recommender.coverage import IdentityCoverage, identity_coverage
from recommender.upstream import upstream_edit_url
from recommender.why import ProvenanceItem, WhyThisArtist, why_this_artist


def _identity_line(why: WhyThisArtist) -> str:
    return f"Identity: {escape(why.identity_statement)}"


def _coverage_html(coverage: IdentityCoverage) -> str:
    rows = [
        f"<li>{escape(label)}: {count}</li>"
        for label, count in coverage.basis_breakdown()
        if count > 0 or label.startswith("Unknown")
    ]
    return (
        '<section aria-labelledby="coverage-h">'
        '<h2 id="coverage-h">Identity coverage</h2>'
        f"<p>{escape(coverage.summary_line())}</p>"
        "<ul>" + "".join(rows) + "</ul><p>This readout is descriptive, not a quota.</p></section>"
    )


def _rank_shift_line(why: WhyThisArtist) -> str:
    return f"Rank shift: {escape(why.rank_shift)}"


def _fix_at_source_link(item: ProvenanceItem) -> str:
    edit_url = upstream_edit_url(item.source_kind, item.citation)
    if edit_url is None:
        return ""
    return (
        f' <a class="fix-at-source" href="{escape(edit_url)}">'
        f"Fix at source: correct this {escape(item.source_kind)} claim upstream</a>"
    )


def _conflict_html(why: WhyThisArtist, aid: str) -> str:
    """Render source disagreement as text and structure, never colour alone."""
    if not why.conflict_note:
        return ""
    return (
        f'<div class="conflict" role="note" aria-labelledby="conflict-h-{aid}">'
        f'<p id="conflict-h-{aid}" class="conflict-heading">Sources disagree</p>'
        f"<p>{escape(why.conflict_note)}</p></div>"
    )


def _provenance_html(why: WhyThisArtist, aid: str) -> str:
    """Identity provenance: each citation with the *raw value the source asserted*.

    Showing the asserted value (not just a label) is what makes "sourced, never
    inferred" auditable rather than a promise.
    """
    if not why.provenance:
        return '<p class="sources">Sources: none — identity unknown, surfaced on merit.</p>'
    items = "".join(
        f"<li>{escape(p.source_kind)} asserted “{escape(p.asserted_value)}”: "
        f'<a href="{escape(p.citation)}">{escape(p.citation)}</a> '
        f'<span class="retrieved">(retrieved {escape(p.retrieved_at)})</span>'
        + (
            '<span class="local-correction"> — local correction</span>'
            if p.is_local_correction
            else ""
        )
        + _fix_at_source_link(p)
        + "</li>"
        for p in why.provenance
    )
    return (
        f'<p class="sources" id="src-{aid}">Sources (sourced, never inferred):</p><ul>{items}</ul>'
    )


def _reasons_html(why: WhyThisArtist) -> str:
    items = "".join(f"<li>{escape(r)}</li>" for r in why.reasons)
    return f"<ul>{items}</ul>"


def _card_html(rec: Recommendation) -> str:
    why = why_this_artist(rec)
    basis = escape(str(why.identity_basis))
    aid = escape(rec.artist.artist_id)
    return (
        f'<article class="card" aria-labelledby="h-{aid}">'
        f'<h3 id="h-{aid}">{rec.rank}. {escape(rec.artist.name)}</h3>'
        f'<p class="score">Score: {rec.score:.3f} '
        f"(taste {rec.base_score:.3f} + values lens {rec.rerank_delta:.3f})</p>"
        f'<p class="identity" data-basis="{basis}" data-inferred="false">'
        f"{_identity_line(why)}</p>"
        f'<p class="rank-shift">{_rank_shift_line(why)}</p>'
        f"{_conflict_html(why, aid)}"
        f"<h4>Why this artist</h4>{_reasons_html(why)}"
        f"{_provenance_html(why, aid)}"
        f'<p class="summary">{escape(rec.explanation.summary)}</p>'
        f"</article>"
    )


def _row_html(r: Recommendation) -> str:
    return (
        f"<tr><td>{r.rank}</td>"
        f'<th scope="row">{escape(r.artist.name)}</th>'
        f"<td>{r.base_score:.3f}</td><td>{r.rerank_delta:.3f}</td>"
        f"<td>{r.score:.3f}</td>"
        f"<td>{escape(str(r.explanation.identity_basis))}</td></tr>"
    )


def _scroll_region(label: str, table_html: str) -> str:
    """Wrap a data table in a keyboard-operable horizontal-scroll region.

    WCAG 2.2 §1.4.10 (Reflow) excepts data tables — genuinely two-dimensional
    content — from the 320 px no-horizontal-scroll requirement, but only the
    table itself may scroll: the *page* must still reflow. ``overflow-x: auto``
    scopes the scrolling to this wrapper, and ``tabindex="0"`` +
    ``role="region"`` + an accessible name make the region reachable and
    announceable for keyboard/AT users (axe: scrollable-region-focusable), so
    Arrow keys can scroll it without a pointer (§2.1.1).
    """
    return (
        f'<div class="table-scroll" role="region" tabindex="0" aria-label="{escape(label)}">'
        f"{table_html}</div>"
    )


def _table_html(recs: Sequence[Recommendation]) -> str:
    rows = "".join(_row_html(r) for r in recs)
    return _scroll_region(
        "Recommendation scores table (scrolls sideways on narrow screens)",
        "<table><caption>Recommendation scores (data-table equivalent of "
        "the score chart)</caption><thead><tr>"
        '<th scope="col">Rank</th><th scope="col">Artist</th>'
        '<th scope="col">Taste score</th><th scope="col">Values boost</th>'
        '<th scope="col">Total</th><th scope="col">Identity basis</th>'
        f"</tr></thead><tbody>{rows}</tbody></table>",
    )


def _exposure_panel_html(panel: dict[str, object] | None) -> str:
    if panel is None:
        return ""
    rows = cast("list[dict[str, object]]", panel["exposure_rows"])
    k = cast(int, panel["k"])
    base_pct = f"{cast(float, panel['base_lens']):.0%}"
    current_pct = f"{cast(float, panel['current_lens']):.0%}"
    body = "".join(
        f'<tr><th scope="row">{escape(str(row["segment"]))}</th>'
        f"<td>{cast(float, row['base_share']):.0%}</td>"
        f"<td>{cast(float, row['current_share']):.0%}</td></tr>"
        for row in rows
    )
    retention = cast("dict[str, object]", panel["retention_row"])
    by_lens = cast("dict[str, float]", retention["by_lens"])
    retention_headers = "".join(f'<th scope="col">Lens {escape(key)}</th>' for key in by_lens)
    retention_cells = "".join(f"<td>{value:.0%}</td>" for value in by_lens.values())
    return (
        "<h2>Fairness observability</h2>"
        "<p>Exposure changes are shown alongside the merge-blocking "
        "unknown-retention guarantee.</p>"
        + _scroll_region(
            "Exposure share table (scrolls sideways on narrow screens)",
            f"<table><caption>Top-{k} exposure share by identity segment — base lens "
            f"({base_pct}) vs current lens ({current_pct})</caption><thead><tr>"
            '<th scope="col">Identity segment</th><th scope="col">Base share</th>'
            f'<th scope="col">Current share</th></tr></thead><tbody>{body}</tbody></table>',
        )
        + _scroll_region(
            "Unknown-identity retention table (scrolls sideways on narrow screens)",
            "<table><caption>Unknown-identity retention across the lens</caption><thead><tr>"
            f'<th scope="col">Identity segment</th>{retention_headers}</tr></thead><tbody><tr>'
            f'<th scope="row">{escape(str(retention["segment"]))}</th>{retention_cells}'
            "</tr></tbody></table>",
        )
    )


#: Explicit per-scheme design tokens (BUG-1 fix). The old stylesheet declared
#: ``color-scheme: light dark`` with **no** explicit colours, so under an OS dark
#: theme every pair fell back to UA defaults and produced real axe contrast
#: failures. Both palettes below are unit-tested against WCAG 2.2 relative
#: luminance in ``tests/test_contrast.py`` (merge-blocking); the ratios in the
#: comments are computed, not aspirational.
LIGHT_TOKENS: dict[str, str] = {
    "bg": "#ffffff",
    "text": "#1b1b1b",  # 17.22:1 vs bg (AAA body text; target >= 7:1)
    "link": "#0b57d0",  # 6.39:1 vs bg (AA text; >= 4.5:1)
    "border": "#595959",  # 7.00:1 vs bg (non-text; >= 3:1)
    "focus": "#0b57d0",  # 6.39:1 vs bg (focus indicator; >= 3:1)
}

DARK_TOKENS: dict[str, str] = {
    "bg": "#121212",
    "text": "#e8e8e8",  # 15.29:1 vs bg (AAA body text; target >= 7:1)
    "link": "#8ab4f8",  # 8.89:1 vs bg (AA text; >= 4.5:1)
    "border": "#9e9e9e",  # 6.99:1 vs bg (non-text; >= 3:1)
    "focus": "#8ab4f8",  # 8.89:1 vs bg (focus indicator; >= 3:1)
}

#: Schemes accepted by :func:`render_cards_html` / ``python -m app.build_static``.
#: ``auto`` ships both palettes behind ``prefers-color-scheme``; ``light``/``dark``
#: pin one palette unconditionally so the a11y gate can audit **both** renderings
#: deterministically on any machine (local Dark-Mode Mac and light-mode CI alike).
SCHEMES: tuple[str, ...] = ("auto", "light", "dark")


def _css_vars(tokens: dict[str, str]) -> str:
    return " ".join(f"--{name}: {value};" for name, value in tokens.items())


def _style(scheme: str = "auto") -> str:
    if scheme not in SCHEMES:
        raise ValueError(f"scheme must be one of {SCHEMES}, got {scheme!r}")
    if scheme == "auto":
        root = (
            f":root {{ color-scheme: light dark; {_css_vars(LIGHT_TOKENS)} }}\n"
            f"@media (prefers-color-scheme: dark) {{\n"
            f"  :root {{ {_css_vars(DARK_TOKENS)} }}\n"
            f"}}"
        )
    else:
        tokens = LIGHT_TOKENS if scheme == "light" else DARK_TOKENS
        root = f":root {{ color-scheme: {scheme}; {_css_vars(tokens)} }}"
    return f"""
{root}
body {{ font-family: system-ui, sans-serif; max-width: 70ch; margin: 0 auto; padding: 1rem;
       background: var(--bg); color: var(--text); }}
a {{ color: var(--link); }}
.card {{ border: 1px solid var(--border); border-radius: 8px; padding: 1rem; margin: 1rem 0; }}
.identity {{ font-weight: 600; }}
.identity::before {{ content: "\\25CF  "; }}  /* glyph paired with text, not colour-only;
                                                inherits --text, never colour-only meaning */
.conflict {{ border: 2px dashed var(--border); border-radius: 6px;
             padding: 0.5rem 0.75rem; margin: 0.5rem 0; }}
.conflict-heading {{ font-weight: 700; margin: 0 0 0.25rem; }}
.conflict-heading::before {{ content: "\\26A0  "; }}
.local-correction {{ font-style: italic; }}
a:focus, .skip:focus, .table-scroll:focus {{ outline: 3px solid var(--focus); }}
.skip {{ position: absolute; left: -999px; }}
.skip:focus {{ left: 1rem; top: 1rem; background: var(--bg); }}
/* Reflow (WCAG 1.4.10): data tables are excepted 2-D content, but only the
   table may scroll — the wrapper scopes it so the page itself never scrolls
   horizontally at 320 px. Keyboard-focusable, so Arrow keys can drive it. */
.table-scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid var(--border); padding: 0.4rem; text-align: left; }}
/* Long unbroken citation URLs must wrap rather than force horizontal scroll. */
.sources + ul a, .sources a {{ overflow-wrap: anywhere; }}
@media (prefers-reduced-motion: reduce) {{
  * {{ animation: none !important; transition: none !important; }}
}}
"""


def render_cards_html(
    recs: Sequence[Recommendation],
    lens_strength: float,
    username: str = "demo",
    scheme: str = "auto",
    exposure_panel: dict[str, object] | None = None,
) -> str:
    """Render a complete, accessible HTML document for the given recommendations.

    ``scheme="auto"`` (the shipped default) responds to ``prefers-color-scheme``;
    ``"light"``/``"dark"`` pin that palette so the a11y gate audits both schemes.
    """
    cards = "".join(_card_html(r) for r in recs)
    coverage_html = _coverage_html(identity_coverage(recs))
    lens_pct = f"{lens_strength:.0%}"
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Women-Artist Discovery — recommendations</title>"
        f"<style>{_style(scheme)}</style></head><body>"
        '<a class="skip" href="#main">Skip to recommendations</a>'
        "<header><h1>Women-Artist Discovery</h1>"
        f"<p>Recommendations for <strong>{escape(username)}</strong>. "
        f"The values lens is set to <strong>{lens_pct}</strong>: it only ever "
        "<em>boosts</em> artists whose identity is sourced as a woman (cis or "
        "trans — no distinction is drawn) or a nonbinary person, and bands whose "
        "sourced lineup is fronted by one of them. Each front-person's gender is "
        "shown as their source stated it. It never lowers anyone's score, and "
        "artists with unknown identity are surfaced on musical merit alone."
        "</p></header>"
        '<main id="main">'
        f"{coverage_html}"
        "<h2>Score summary</h2>"
        f"{_table_html(recs)}"
        f"{_exposure_panel_html(exposure_panel)}"
        "<h2>Recommendations</h2>"
        f"{cards}"
        "</main></body></html>"
    )
