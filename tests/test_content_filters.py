"""Identity-blind tag and era filters (#123).

Four properties, in the order a regression in them would matter:

1. **A filter is identity-blind.** Two guards: an AST scan of the whole module against the
   same forbidden set the diversifier is held to, and a property test that the identity mix
   of the filtered pool equals the identity mix of the unfiltered pool restricted to the same
   tags. The first says the code cannot read identity; the second says the *outcome* does not
   depend on it either.
2. **Absence never excludes.** An artist with no tags, or no known start year, survives every
   filter. Upstream metadata coverage is thinnest for the least documented artists, so a
   filter that dropped them would re-impose the popularity bias the ranking resists, while
   looking like a neutral content preference.
3. **Filters do not disturb unknown-identity artists' standing.** The existing first-class
   guarantee holds under filtering.
4. **A filter that matches nothing is an empty result, not an error.**
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pipeline import cli
from pipeline.models import Artist, Gender, ListeningProfile
from recommender import content_filters as content_filters_module
from recommender.content_filters import (
    MAX_YEAR,
    MIN_YEAR,
    NO_FILTER,
    ContentFilter,
    FilterSpecError,
    normalize_tag,
    normalize_tags,
)
from recommender.hybrid import recommend

from .conftest import make_artist
from .test_diversify import FORBIDDEN_ATTRS

# --- 1. identity-blindness ---------------------------------------------------------------


def test_the_filter_module_never_reads_a_forbidden_attribute() -> None:
    """The same whole-module AST scan `diversify.py` is held to (EXP-04).

    This is why the content filters are a module of their own rather than an extension of
    `recommender/filters.py`: that module holds `is_sourced_man_only`, which reads a sourced
    gender on purpose, so the two cannot share a file without weakening this scan to a
    per-function allowlist.
    """
    tree = ast.parse(Path(content_filters_module.__file__).read_text(encoding="utf-8"))
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute):
                    referenced.add(sub.attr.lower())
                elif isinstance(sub, ast.Name):
                    referenced.add(sub.id.lower())
    leaked = referenced & FORBIDDEN_ATTRS
    assert not leaked, f"content_filters.py references forbidden attributes: {leaked}"


def test_the_filter_module_is_never_handed_an_artist_at_all() -> None:
    """Stronger than the scan: the module imports no model type, so there is no object in
    scope that *has* an identity. A future `keeps(artist)` convenience would break this."""
    tree = ast.parse(Path(content_filters_module.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(module.startswith(("pipeline", "recommender")) for module in imported), (
        f"content_filters.py imports project modules: {imported}"
    )


# --- normalization -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Dream Pop", "dream pop"),
        ("dream-pop", "dream pop"),
        ("dream_pop", "dream pop"),
        ("  DREAM   POP ", "dream pop"),
    ],
)
def test_tag_spellings_that_mean_one_tag_fold_to_one_tag(raw: str, expected: str) -> None:
    assert normalize_tag(raw) == expected


def test_a_bare_string_is_refused_rather_than_iterated_by_character() -> None:
    with pytest.raises(TypeError):
        normalize_tags("shoegaze")  # type: ignore[arg-type]


def test_empty_tags_are_dropped_not_kept_as_a_tag() -> None:
    assert normalize_tags(["shoegaze", "  ", ""]) == frozenset({"shoegaze"})


# --- 2. absence never excludes ------------------------------------------------------------


def test_an_artist_with_no_tags_survives_an_include_filter() -> None:
    spec = ContentFilter.build(include_tags=["shoegaze"])
    assert spec.keeps_tags([]) is True


def test_an_artist_with_no_tags_survives_an_exclude_filter() -> None:
    spec = ContentFilter.build(exclude_tags=["country"])
    assert spec.keeps_tags([]) is True


def test_an_artist_with_no_known_start_year_survives_both_bounds() -> None:
    spec = ContentFilter.build(year_from=2015, year_to=2025)
    assert spec.keeps_year(None) is True


def test_an_include_filter_keeps_only_artists_carrying_a_listed_tag() -> None:
    spec = ContentFilter.build(include_tags=["Shoegaze", "dream-pop"])
    assert spec.keeps_tags(["shoegaze", "noise"]) is True
    assert spec.keeps_tags(["DREAM POP"]) is True
    assert spec.keeps_tags(["country"]) is False


def test_an_exclude_filter_removes_on_a_single_match() -> None:
    spec = ContentFilter.build(exclude_tags=["country"])
    assert spec.keeps_tags(["indie", "Country"]) is False
    assert spec.keeps_tags(["indie"]) is True


def test_year_bounds_are_inclusive_and_exclude_only_known_years() -> None:
    spec = ContentFilter.build(year_from=2015, year_to=2025)
    assert spec.keeps_year(2015) is True
    assert spec.keeps_year(2025) is True
    assert spec.keeps_year(2014) is False
    assert spec.keeps_year(2026) is False


# --- filter specification -----------------------------------------------------------------


def test_the_default_filter_is_inert() -> None:
    assert NO_FILTER.active is False
    assert NO_FILTER.keeps_tags(["anything"]) is True
    assert NO_FILTER.keeps_year(1999) is True
    assert NO_FILTER.describe() == "filters: none"


def test_a_tag_both_included_and_excluded_is_refused_not_silently_decided() -> None:
    with pytest.raises(FilterSpecError, match="shoegaze"):
        ContentFilter.build(include_tags=["Shoegaze"], exclude_tags=["SHOEGAZE"])


def test_an_inverted_year_range_is_refused() -> None:
    with pytest.raises(FilterSpecError, match="matches nothing"):
        ContentFilter.build(year_from=2025, year_to=2015)


@pytest.mark.parametrize("year", [MIN_YEAR - 1, MAX_YEAR + 1])
def test_a_year_outside_the_accepted_span_is_refused_not_clamped(year: int) -> None:
    with pytest.raises(FilterSpecError):
        ContentFilter.build(year_from=year)


def test_describe_names_every_active_bound_and_the_absence_rule() -> None:
    spec = ContentFilter.build(
        include_tags=["shoegaze"], exclude_tags=["country"], year_from=2015, year_to=2025
    )
    line = spec.describe()
    assert "include-tags=shoegaze" in line
    assert "exclude-tags=country" in line
    assert "year-from=2015" in line
    assert "year-to=2025" in line
    assert "no known start year, are kept" in line


# --- 3. behavior through the recommender -------------------------------------------------


class _NoSimilarity:
    """A scrobble source with no collaborative signal, so content tags decide the pool."""

    def similar_artists(self, artist_id: str) -> list[object]:
        return []

    def artist_tags(self, artist_id: str) -> tuple[str, ...]:
        return ()


def _world() -> tuple[ListeningProfile, dict[str, Artist]]:
    """One seed the listener plays, and five candidates spanning tags, years and identity."""
    profile = ListeningProfile(
        username="tester",
        play_counts={"seed": 10.0},
        artist_names={"seed": "Seed"},
        tags={"seed": ("shoegaze", "dream pop")},
    )
    catalog = {
        "seed": make_artist("seed", tags=("shoegaze", "dream pop")),
        # sourced woman, tagged, recent
        "a": make_artist("a", Gender.WOMAN, tags=("shoegaze",), career_start_year=2018),
        # unknown identity, tagged, recent
        "b": make_artist("b", tags=("shoegaze",), career_start_year=2019),
        # sourced woman, tagged, old
        "c": make_artist("c", Gender.WOMAN, tags=("dream pop",), career_start_year=1990),
        # unknown identity, no tags, no year -- must survive every filter
        "d": make_artist("d"),
        # sourced man, wrong tag
        "e": make_artist("e", Gender.MAN, tags=("country",), career_start_year=2020),
    }
    return profile, catalog


def _ids(profile: ListeningProfile, catalog: dict[str, Artist], **kw: object) -> list[str]:
    recs = recommend(profile, catalog, _NoSimilarity(), k=10, **kw)  # type: ignore[arg-type]
    return [rec.artist.artist_id for rec in recs]


def test_include_tags_returns_matching_artists_plus_every_untagged_one() -> None:
    profile, catalog = _world()
    got = set(_ids(profile, catalog, content_filter=ContentFilter.build(include_tags=["shoegaze"])))
    assert "a" in got and "b" in got
    assert "d" in got, "an artist with no tags must survive an include filter"
    assert "c" not in got and "e" not in got


def test_year_from_excludes_a_known_earlier_year_and_keeps_an_unknown_one() -> None:
    profile, catalog = _world()
    got = set(_ids(profile, catalog, content_filter=ContentFilter.build(year_from=2015)))
    assert "c" not in got, "1990 is known and earlier"
    assert "d" in got, "an unknown start year must be kept"
    assert {"a", "b", "e"} <= got


def test_exclude_tags_removes_only_a_positive_match() -> None:
    profile, catalog = _world()
    got = set(_ids(profile, catalog, content_filter=ContentFilter.build(exclude_tags=["country"])))
    assert "e" not in got
    assert {"a", "b", "c", "d"} <= got


def test_a_filter_matching_nothing_returns_an_empty_list_not_an_error() -> None:
    profile, catalog = _world()
    catalog = {aid: artist for aid, artist in catalog.items() if aid in {"seed", "a"}}
    got = _ids(
        profile, catalog, content_filter=ContentFilter.build(include_tags=["nonexistent-genre"])
    )
    assert got == []


def test_the_default_call_is_unchanged_by_the_new_parameter() -> None:
    profile, catalog = _world()
    assert _ids(profile, catalog) == _ids(profile, catalog, content_filter=NO_FILTER)


# --- 1b. the outcome does not depend on identity either -----------------------------------


def test_the_filtered_pool_has_the_identity_mix_of_the_pool_restricted_to_the_same_tags() -> None:
    """A filter cannot shift the identity composition of what survives beyond what the tag
    predicate alone determines. Computed both ways and compared: if the filter ever read an
    identity field, the two sets would diverge."""
    profile, catalog = _world()
    spec = ContentFilter.build(include_tags=["shoegaze"])

    through_the_filter = set(_ids(profile, catalog, content_filter=spec))
    unfiltered = set(_ids(profile, catalog))
    by_the_predicate = {
        aid
        for aid in unfiltered
        if spec.keeps_tags(catalog[aid].tags) and spec.keeps_year(catalog[aid].career_start_year)
    }
    assert through_the_filter == by_the_predicate

    def mix(ids: set[str]) -> dict[Gender, int]:
        counts: dict[Gender, int] = {}
        for aid in ids:
            gender = catalog[aid].identity.gender
            counts[gender] = counts.get(gender, 0) + 1
        return counts

    assert mix(through_the_filter) == mix(by_the_predicate)


def test_unknown_artists_keep_their_standing_relative_to_sourced_ones_under_filters() -> None:
    """The first-class-unknown guarantee, re-run with a filter active."""
    profile, catalog = _world()
    spec = ContentFilter.build(include_tags=["shoegaze"])
    surviving = {"a", "b", "d"}

    unfiltered = [aid for aid in _ids(profile, catalog, lens_strength=1.0) if aid in surviving]
    filtered = _ids(profile, catalog, lens_strength=1.0, content_filter=spec)
    assert filtered == unfiltered


# --- 4. the CLI surfaces --------------------------------------------------------------------


def _demo_tags() -> set[str]:
    """Tags carried by more than one artist in the demo world, so a filtered run is
    non-empty and the filter is doing visible work."""
    from collections import Counter

    from pipeline.demo import demo_catalog

    counts = Counter(
        normalize_tag(tag) for artist in demo_catalog().values() for tag in artist.tags
    )
    return {tag for tag, n in counts.items() if n > 1}


def test_recommend_states_the_active_filters(capsys: pytest.CaptureFixture[str]) -> None:
    tag = sorted(_demo_tags())[0]
    assert cli.main(["recommend", "--include-tags", tag]) == 0
    out = capsys.readouterr().out
    assert f"include-tags={tag}" in out
    assert "no known start year, are kept" in out


def test_recommend_says_nothing_about_filters_when_none_are_set(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["recommend"]) == 0
    assert "filters:" not in capsys.readouterr().out


def test_recommend_reports_an_empty_result_rather_than_pretending(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["recommend", "--include-tags", "a-tag-nobody-uses"]) == 0
    out = capsys.readouterr().out
    assert "no artist in this world matches those filters" in out


def test_a_contradictory_filter_exits_two_with_the_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(["recommend", "--include-tags", "indie", "--exclude-tags", "Indie"])
    assert code == 2
    assert "both included and excluded" in capsys.readouterr().err


def test_an_out_of_range_year_is_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit):
        cli.main(["recommend", "--year-from", "1200"])


def test_report_renders_the_filters_line_only_when_filters_are_active(tmp_path: Path) -> None:
    """The unfiltered render must stay byte-identical: `docs/audits/dashboard.html` is a
    committed gate input, and moving it to add a sentence saying nothing happened would be a
    change to published evidence."""
    plain = tmp_path / "plain.html"
    filtered = tmp_path / "filtered.html"
    tag = sorted(_demo_tags())[0]
    assert cli.main(["report", "--out", str(plain)]) == 0
    assert cli.main(["report", "--out", str(filtered), "--include-tags", tag]) == 0
    plain_html = plain.read_text(encoding="utf-8")
    assert 'class="filters"' not in plain_html
    assert f"include-tags={tag}" in filtered.read_text(encoding="utf-8")


def test_a_filtered_report_still_passes_the_a11y_gate(tmp_path: Path) -> None:
    from app.a11y_check import check_html

    out = tmp_path / "filtered.html"
    tag = sorted(_demo_tags())[0]
    assert cli.main(["report", "--out", str(out), "--include-tags", tag]) == 0
    violations = check_html(out.read_text(encoding="utf-8"))
    assert violations == [], violations


def test_export_states_the_filters_on_stderr_not_in_the_playlist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The export file is contracted to carry artist and track names only."""
    out = tmp_path / "picks.m3u"
    tag = sorted(_demo_tags())[0]
    assert cli.main(["export", "--out", str(out), "--include-tags", tag]) == 0
    captured = capsys.readouterr()
    assert f"include-tags={tag}" in captured.err
    assert "filters:" not in out.read_text(encoding="utf-8")


def test_every_filter_flag_is_accepted_by_all_three_surfaces() -> None:
    parser = cli.build_parser()
    actions = {
        choice: {opt for action in sub._actions for opt in action.option_strings}
        for choice, sub in _subparsers(parser).items()
    }
    wanted = {"--include-tags", "--exclude-tags", "--year-from", "--year-to"}
    for command in ("recommend", "report", "export"):
        assert wanted <= actions[command], command


def _subparsers(parser: object) -> dict[str, object]:
    import argparse as _argparse

    for action in parser._actions:  # type: ignore[attr-defined]
        if isinstance(action, _argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("no subparsers found")


# --- the year field itself -------------------------------------------------------------------


def test_a_musicbrainz_life_span_begin_yields_its_year() -> None:
    from pipeline.enrich import parse_musicbrainz_life_span_begin as parse

    assert parse({"life-span": {"begin": "1994"}}) == 1994
    assert parse({"life-span": {"begin": "1994-03"}}) == 1994
    assert parse({"life-span": {"begin": "1994-03-21"}}) == 1994


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"life-span": None},
        {"life-span": {}},
        {"life-span": {"begin": None}},
        {"life-span": {"begin": ""}},
        {"life-span": {"begin": "sometime in the nineties"}},
        {"life-span": {"begin": "94"}},
    ],
)
def test_an_unreadable_begin_value_is_unknown_and_never_a_plausible_number(
    payload: dict[str, object],
) -> None:
    from pipeline.enrich import parse_musicbrainz_life_span_begin as parse

    assert parse(payload) is None


def test_a_stored_non_integer_year_decodes_to_unknown() -> None:
    """A coerced garbage year would silently drop real artists from a filtered run."""
    from pipeline.serde import artist_from_dict, artist_to_dict

    encoded = artist_to_dict(make_artist("a", career_start_year=2018))
    assert artist_from_dict(encoded).career_start_year == 2018

    for bad in ("2018", 2018.5, True, None, []):
        encoded["career_start_year"] = bad
        assert artist_from_dict(encoded).career_start_year is None, bad


def test_a_payload_cached_before_the_field_existed_decodes_to_unknown() -> None:
    from pipeline.serde import artist_from_dict, artist_to_dict

    encoded = artist_to_dict(make_artist("a", career_start_year=2018))
    del encoded["career_start_year"]
    assert artist_from_dict(encoded).career_start_year is None


def test_an_enricher_without_the_optional_protocol_yields_no_year() -> None:
    """`CareerSpanSource` is a second, optional protocol precisely so that an enricher which
    does not implement it is not penalized: it yields unknown, and unknown is kept."""
    from pipeline.enrich import FixtureEnricher
    from pipeline.ingest import enrich_artist

    class _Bare:
        def gender_evidence(self, artist_id: str) -> list[object]:
            return []

        def orientation_evidence(self, artist_id: str) -> list[object]:
            return []

        def composition_evidence(self, artist_id: str) -> tuple[list[object], list[object]]:
            return ([], [])

    class _Source:
        def artist_tags(self, artist_id: str) -> tuple[str, ...]:
            return ("shoegaze",)

    bare = enrich_artist("a", "A", _Source(), _Bare())  # type: ignore[arg-type]
    assert bare.career_start_year is None

    knows = enrich_artist(
        "a", "A", _Source(), FixtureEnricher({}, {}, career_start_years={"a": 2018})
    )  # type: ignore[arg-type]
    assert knows.career_start_year == 2018
