"""Accessibility gate (mechanical subset): the rendered cards have 0 violations.

The browser-based pa11y/axe run happens in CI via ``make a11y``; this asserts the
static-render contract that pa11y also enforces, so regressions in markup
semantics fail fast in the unit suite too.
"""

from __future__ import annotations

from pathlib import Path

from app import a11y_check as app_a11y
from app.a11y_check import (
    FAMILIES,
    audit,
    check_html,
    unreached_widget_call_sites,
)
from app.a11y_check import main as a11y_main
from app.render import render_cards_html
from pipeline.models import Explanation, IdentityBasis, Recommendation, Signal
from recommender.exposure import observability_panel
from recommender.hybrid import recommend


def _html(profile, catalog, source, lens=0.5):
    recs = recommend(profile, catalog, source, k=10, lens_strength=lens)
    return render_cards_html(recs, lens_strength=lens, username="demo")


def _html_with_observability(profile, catalog, source, lens=0.5):
    recs_by_lens = {
        value: recommend(profile, catalog, source, k=10, lens_strength=value)
        for value in {0.0, 0.25, 0.5, 0.75, 1.0, lens}
    }
    panel = observability_panel(recs_by_lens, current_lens=lens, k=10)
    return render_cards_html(
        recs_by_lens[lens], lens_strength=lens, username="demo", exposure_panel=panel
    )


def _wrap_as_recommendation(artist, rank: int = 1) -> Recommendation:
    """Wrap a catalog artist in a minimal Recommendation, honestly carrying its
    actual sourced identity basis/sources — used to exercise a specific
    artist's provenance in isolation, independent of who `recommend()`
    happens to surface for the default demo profile."""
    if artist.identity.is_known:
        basis = IdentityBasis.SELF_IDENTIFIED
        sources = artist.identity.sources
    elif artist.composition is not None and artist.sourced_front_genders:
        basis = IdentityBasis.BAND_COMPOSITION
        sources = artist.composition.sources
    else:
        basis = IdentityBasis.UNKNOWN
        sources = ()
    expl = Explanation(
        signals=(Signal(kind="content", detail="test fixture signal", weight=1.0),),
        identity_basis=basis,
        identity_sources=sources,
        summary=f"test summary for {artist.name}",
    )
    return Recommendation(
        artist=artist, base_score=1.0, rerank_delta=0.0, explanation=expl, rank=rank
    )


def test_rendered_dashboard_has_zero_a11y_violations(profile, catalog, source) -> None:
    violations = check_html(_html(profile, catalog, source))
    assert violations == [], violations


def test_observability_panel_is_table_first_and_accessible(profile, catalog, source) -> None:
    html = _html_with_observability(profile, catalog, source)
    assert check_html(html) == []
    assert "Fairness observability" in html
    assert "exposure share by identity segment" in html
    assert "Rank-protected retention" in html
    # Both rank-protected segments get a row, not just unknown (#68).
    assert '<th scope="row">unknown</th>' in html
    assert '<th scope="row">other</th>' in html
    assert html.count("<table>") >= 3


def test_identity_is_text_not_color_only(profile, catalog, source) -> None:
    html = _html(profile, catalog, source)
    assert "Identity:" in html
    assert "unknown — surfaced on musical similarity alone" in html


def test_score_chart_has_data_table_equivalent(profile, catalog, source) -> None:
    html = _html(profile, catalog, source)
    assert "<table>" in html and "<caption>" in html
    assert 'scope="col"' in html


def test_sources_render_as_links(profile, catalog, source) -> None:
    html = _html(profile, catalog, source)
    assert 'href="https://' in html


def test_fix_at_source_link_appears_for_individual_identity_sources(catalog) -> None:
    """A sourced individual identity (wikidata/musicbrainz) gets a labeled
    "Fix at source" link — descriptive text, never color/icon alone."""
    recs = [
        _wrap_as_recommendation(catalog["mitski"], rank=1),  # wikidata-p21 + musicbrainz-gender
        _wrap_as_recommendation(catalog["snail-mail"], rank=2),  # musicbrainz-gender
    ]
    html = render_cards_html(recs, lens_strength=0.5, username="demo")
    assert check_html(html) == []
    assert 'class="fix-at-source"' in html
    assert "Fix at source: correct this wikidata-p21 claim upstream" in html
    assert "Fix at source: correct this musicbrainz-gender claim upstream" in html


def _card(html: str, needle: str) -> str:
    # The score-summary table repeats every artist name before the card list
    # does, so search only within the cards section (after the section
    # heading) to land on the actual <article> card, not the table row.
    cards_start = html.index("<h2>Recommendations</h2>")
    idx = html.index(needle, cards_start)
    end = html.index("</article>", idx)
    return html[idx:end]


def test_fix_at_source_link_absent_for_unknown_identity_card(profile, catalog, source) -> None:
    """ "Mystery Act" (first-class unknown) carries no provenance, so no fix link."""
    html = _html(profile, catalog, source, lens=1.0)
    assert "Mystery Act" in html
    card = _card(html, "Mystery Act")
    assert "none — identity unknown, surfaced on merit" in card
    assert "fix-at-source" not in card


def test_fix_at_source_link_absent_for_band_composition_only_source(
    profile, catalog, source
) -> None:
    """ "boygenius" is sourced only via Discogs lineup — no defined edit surface."""
    html = _html(profile, catalog, source)
    assert "boygenius" in html
    card = _card(html, "boygenius")
    assert "discogs-lineup" in card
    assert "fix-at-source" not in card


def test_checker_flags_bad_html() -> None:
    bad = "<html><body><h1>a</h1><h3>skip</h3><table><th>x</th></table></body></html>"
    violations = check_html(bad)
    assert any("lang" in v for v in violations)
    assert any("viewport" in v for v in violations)
    assert any("skip link" in v for v in violations)
    assert any("scope" in v for v in violations)
    assert any("caption" in v for v in violations)
    assert any("jumps" in v for v in violations)


# --- The fallback gate's own entry point ------------------------------------
#
# `make a11y` prefers pa11y and falls back to `python -m app.a11y_check FILE`
# when it is not installed, which is the ordinary case on a fresh machine. That
# fallback *is* the accessibility gate there, and until this block nothing
# executed its `main()`: not one test called it, and `app/` was outside the
# coverage measurement entirely (`addopts` had no `--cov=app`), so the absence
# did not show up as a gap either. A `main()` that returned 0 unconditionally
# would have made `make a11y` green on every page forever, and the suite would
# have agreed. Three of the checker's own violation detectors — empty `href`,
# missing `alt`, empty link text — and the h1-count check were unexercised for
# the same reason.


def test_checker_flags_a_missing_alt_attribute() -> None:
    assert "img without alt attribute" in check_html('<img src="x.png">')
    # An explicitly empty alt is the correct markup for a decorative image and
    # must not be reported; asserting this is what stops the detector being
    # "flag every img", which would fail the real render and get deleted.
    assert "img without alt attribute" not in check_html('<img src="x.png" alt="">')
    assert "img without alt attribute" not in check_html('<img src="x.png" alt="a chart">')


def test_checker_flags_an_anchor_with_no_href_and_no_text() -> None:
    violations = check_html("<a></a>")
    assert "anchor with empty href" in violations
    assert "link with no accessible text" in violations
    assert "link with no accessible text" not in check_html('<a href="/x">Read more</a>')
    assert "anchor with empty href" not in check_html('<a href="/x">Read more</a>')


def test_checker_flags_the_wrong_number_of_h1s() -> None:
    assert any("found 0" in v for v in check_html("<p>no heading at all</p>"))
    assert any("found 2" in v for v in check_html("<h1>a</h1><h1>b</h1>"))


def test_checker_flags_a_missing_main_landmark() -> None:
    assert "no <main> landmark" in check_html("<h1>a</h1>")
    assert "no <main> landmark" not in check_html("<main><h1>a</h1></main>")
    assert "no <main> landmark" not in check_html('<div role="main"><h1>a</h1></div>')


def test_the_fallback_gate_entry_point_exits_non_zero_on_a_violation(tmp_path) -> None:
    """The check that `make a11y` actually runs when pa11y is absent.

    Asserted in both directions on purpose: a gate only ever shown passing is
    indistinguishable from one that cannot fail.
    """
    bad = tmp_path / "bad.html"
    bad.write_text("<html><body><h1>a</h1></body></html>", encoding="utf-8")
    assert a11y_main([str(bad)]) == 1


def test_the_fallback_gate_entry_point_exits_zero_on_the_real_render(
    tmp_path, profile, catalog, source
) -> None:
    page = tmp_path / "dashboard.html"
    page.write_text(_html(profile, catalog, source), encoding="utf-8")
    assert a11y_main([str(page)]) == 0


def test_the_fallback_gate_entry_point_reports_usage_with_no_argument() -> None:
    assert a11y_main([]) == 2


# --- The census: what the gate examined, per family (#139) -------------------
#
# `a11y: 0 violations` was the whole of the gate's output. It is the same
# sentence over a page with twelve interactive controls and over a page with
# none, and the audited artifact is the second kind: `app/render.py` emits no
# control at all, while `app/dashboard.py` builds twelve widgets Streamlit
# renders at run time that no gate here reaches. The disclosure existed in
# README row 6 and in `test_e2e_a11y.py`'s docstring; neither is reachable from
# a green line. These tests hold the replacement to being honest in both
# directions: a family that examined nothing must not report a pass, and a
# family that was supposed to have inputs must fail when it has none.


def test_every_family_declares_exactly_one_of_a_floor_or_a_reason() -> None:
    """The self-limiting half of the family table.

    A family with neither has no answer to "what may you say when you examined
    nothing?", which is how a vacuous pass gets in. A family with both is
    contradictory. Asserting the structure is what stops a family being added
    later without that decision being made.
    """
    assert FAMILIES, "an empty family table would report nothing and pass"
    for family in FAMILIES:
        has_floor = family.floor is not None
        has_reason = family.empty_reason is not None
        assert has_floor != has_reason, (
            f"{family.name}: declare a floor (this page must have inputs here) or a "
            f"written reason (having none is legitimate, and why) — exactly one. "
            f"floor={family.floor!r} reason={family.empty_reason!r}"
        )
        if has_reason:
            assert family.empty_reason and family.empty_reason.strip(), family.name


def test_a_floor_is_structural_rather_than_the_current_count() -> None:
    """A floor equal to today's number is a hand-maintained counter.

    This repository's audited page has 3 tables and 32 `<th>` today. A floor of
    3 or 32 would turn every legitimate render change into a gate failure and
    invite somebody to edit the number rather than read it, which is the shape
    that has jammed merge queues elsewhere in this portfolio. A floor of 1 is a
    claim about structure and never drifts.
    """
    for family in FAMILIES:
        if family.floor is not None:
            assert family.floor == 1, f"{family.name}: floor {family.floor} is a counter"


def test_the_census_names_every_family_on_a_passing_run(profile, catalog, source) -> None:
    report = audit(_html(profile, catalog, source))
    assert report.violations == [], report.violations
    lines = report.census(unreached=12)
    assert len(lines) == len(FAMILIES)
    for family, line in zip(FAMILIES, lines, strict=True):
        assert family.name in line, (family.name, line)
        # Every line carries a verdict a reader can weigh, not just an absence.
        assert any(v in line for v in ("pass", "fail", "not_applicable")), line


def test_a_family_that_examined_nothing_never_reports_a_pass(profile, catalog, source) -> None:
    """The defect, stated as a property.

    `images` and `controls` are empty in the real render. Neither may say
    `pass`; both must carry a written reason instead. This is the assertion
    that distinguishes "no findings" from "no inputs" — the two were previously
    byte-identical.
    """
    report = audit(_html(profile, catalog, source))
    empty = [r for r in report.families if r.examined == 0]
    assert {r.family.name for r in empty} == {"images", "controls"}, [r.family.name for r in empty]
    for result in empty:
        assert result.verdict == "not_applicable", (result.family.name, result.verdict)
        assert result.family.empty_reason
        assert result.family.empty_reason in result.line


def test_the_controls_family_states_the_unreached_streamlit_surface(
    profile, catalog, source
) -> None:
    """Item 3: the disclosure is reachable from the gate's own output.

    Not from README row 6, and not from a test docstring — from the line the
    gate prints on a passing run.
    """
    report = audit(_html(profile, catalog, source))
    line = next(line for line in report.census(unreached=12) if "controls" in line)
    assert "app/dashboard.py" in line
    assert "12 widget call sites" in line
    assert "#139" in line


def test_the_unreached_widget_count_is_measured_from_the_dashboard_source() -> None:
    """The number in the disclosure is derived, not typed.

    It also fails when the scan stops matching: an exemption that no longer
    exempts anything reads exactly like full coverage, so a zero here is a
    broken selector rather than a clean sweep.
    """
    counted = unreached_widget_call_sites()
    assert counted is not None, "app/dashboard.py should be readable from the package"
    assert counted > 0, (
        "the widget scan matched nothing in app/dashboard.py. Either the Streamlit "
        "surface really has no interactive widgets — in which case this gate's "
        "disclosure is stale and should be deleted — or _WIDGET_CALL has stopped "
        "matching and the gate is about to print a reassuring zero"
    )
    # An independent count of the same thing, so the assertion is not the
    # regex agreeing with itself.
    source = (Path(app_a11y.__file__).parent / "dashboard.py").read_text(encoding="utf-8")
    independent = sum(
        source.count(f"st.{widget}(")
        for widget in ("slider", "button", "checkbox", "text_input", "expander")
    )
    assert counted == independent, (counted, independent)


def test_an_unreadable_dashboard_prints_a_sentence_and_never_a_zero(tmp_path) -> None:
    """Absence must not render as a value.

    A missing `app/dashboard.py` means "not counted here", not "nothing is
    unreached". The second is a reassuring number this gate would have no basis
    for, and it is this portfolio's dominant defect.
    """
    assert unreached_widget_call_sites(tmp_path / "does-not-exist.py") is None
    report = audit("<html lang='en'><head><meta name='viewport' content='x'></head>")
    line = next(line for line in report.census(unreached=None) if "controls" in line)
    assert "not counted here" in line
    assert "0 widget call sites" not in line


def test_a_family_with_a_floor_fails_on_an_empty_page() -> None:
    """Item 2: a render that stops emitting tables fails instead of passing.

    The old checker had nothing to say here — a page with no tables produced no
    `table without caption` violation, and no violation is a pass.
    """
    page = (
        "<html lang='en'><head><meta name='viewport' content='width=device-width'>"
        "</head><body><a class='skip' href='#m'>Skip</a><main id='m'><h1>t</h1>"
        "</main></body></html>"
    )
    report = audit(page)
    failed = {r.family.name for r in report.failed}
    # No violations at all, and yet two families are empty that must not be.
    assert report.violations == [], report.violations
    assert failed == {"tables", "table headers"}, failed
    for result in report.failed:
        assert "must have at least 1" in result.line
    # `links` is NOT among them, and the reason is worth recording: the skip
    # link the document family already requires is itself an `<a>`, so a page
    # that satisfies the document rules cannot have zero links. The floor is
    # still declared — it states the family's own contract rather than relying
    # on another family's rule to imply it.
    links = next(r for r in report.families if r.family.name == "links")
    assert links.examined == 1 and links.verdict == "pass"


def test_the_gate_exits_non_zero_when_a_floor_is_breached(tmp_path) -> None:
    """A floor breach reaches the exit code, not just the printed line."""
    page = tmp_path / "empty.html"
    page.write_text(
        "<html lang='en'><head><meta name='viewport' content='width=device-width'>"
        "</head><body><a class='skip' href='#m'>Skip</a><main id='m'><h1>t</h1>"
        "</main></body></html>",
        encoding="utf-8",
    )
    assert a11y_main([str(page)]) == 1


def test_an_interactive_control_is_refused_rather_than_silently_passed() -> None:
    """This checker judges no control, so it must not report on a page with one.

    Counting a `<button>` as examined would produce `1 of 1 controls — pass`
    from a checker that has no accessible-name, label-association or focus-order
    rule. That is the defect one level down, so a control is refused outright
    until such a rule exists.
    """
    violations = check_html("<button></button>")
    assert any("interactive control" in v for v in violations), violations
    assert any("no rule that judges one" in v for v in violations), violations
    # And the family reports it rather than staying silent.
    report = audit("<button></button>")
    controls = next(r for r in report.families if r.family.name == "controls")
    assert controls.examined == 1
    assert controls.verdict == "fail"


def test_the_gate_audits_every_file_it_is_handed(tmp_path) -> None:
    """Reading argv[0] and reporting a pass is the same defect, one layer out.

    `make a11y` loops one file at a time today, so this was latent — and the
    `pa11y` branch beside it takes a list, so the two halves of the same recipe
    disagreed about their own arity.
    """
    good = tmp_path / "good.html"
    good.write_text(
        "<html lang='en'><head><meta name='viewport' content='width=device-width'>"
        "</head><body><a class='skip' href='#m'>Skip</a><main id='m'><h1>t</h1>"
        "<h2>s</h2><a href='/x'>x</a><table><caption>c</caption><tr>"
        "<th scope='col'>h</th></tr></table></main></body></html>",
        encoding="utf-8",
    )
    bad = tmp_path / "bad.html"
    bad.write_text("<html><body><h1>a</h1></body></html>", encoding="utf-8")
    assert a11y_main([str(good)]) == 0
    # The bad file is second: a gate reading only args[0] would return 0 here.
    assert a11y_main([str(good), str(bad)]) == 1
