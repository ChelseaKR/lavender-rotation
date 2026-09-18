"""The docs-figures manifest: its locator, its stamp, and its live rows.

`scripts/docs_figures.py` replaced `scripts/check-readme-claims.py`, which
checked one sentence. The thing worth testing about the replacement is not that
it can compare two integers — it is that the *locator* refuses to guess. A gate
whose regex silently matches nothing passes forever; one that silently matches
twice and then rewrites the first hit falsifies a document. Both are tested here
as errors, because both are how an auto-stamp goes wrong.

The two subprocess-backed derivers (`pytest --collect-only`, `coverage report`)
are deliberately not exercised here — they are thin I/O wrappers, they are run
for real every time `make test` reaches its stage-3 gate step, and calling them
from inside the suite would mean the suite counting itself. That is the same
convention the repo already applies to `scripts/writeup-check.py`. The
file-backed derivers *are* checked against the live documents below, so a stale
figure fails the suite and not only the Makefile step.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_docs_figures() -> ModuleType:
    """`scripts/` is not a package, so load the module by path (as the repo does
    for `scripts/upstream_worklist.py`). Import runs no I/O: the manifest holds
    callables, and nothing calls them until `evaluate()` is asked to."""
    spec = importlib.util.spec_from_file_location(
        "docs_figures", REPO_ROOT / "scripts" / "docs_figures.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["docs_figures"] = module
    spec.loader.exec_module(module)
    return module


df = _load_docs_figures()


DOC = """\
# Title

Intro prose stating 3 widgets, which is not the claim.

## Live section

The gate holds at 85% and nothing else here counts.

### A subsection

Still inside the live section, and it also says 85% once.

## History

On some past day it was 70%.
"""


def _figure(**overrides: object) -> object:
    defaults: dict[str, object] = {
        "name": "test-figure",
        "document": "README.md",
        "section": "## Live section",
        "pattern": re.compile(r"holds at (?P<value>\d+)%"),
        "derive": lambda: "85",
        "source": "a fixture",
    }
    defaults.update(overrides)
    return df.Figure(**defaults)


# --- section scoping -------------------------------------------------------


def test_a_section_ends_at_the_next_heading_of_the_same_level() -> None:
    start, end = df.section_span(DOC, "## Live section")
    body = DOC[start:end]
    assert "The gate holds at 85%" in body
    assert "## History" not in body
    assert "On some past day" not in body


def test_a_section_contains_its_own_subsections() -> None:
    start, end = df.section_span(DOC, "## Live section")
    assert "### A subsection" in DOC[start:end]


def test_the_last_section_runs_to_the_end_of_the_document() -> None:
    start, end = df.section_span(DOC, "## History")
    assert DOC[start:end].strip() == "On some past day it was 70%."


def test_a_missing_section_is_an_error_not_an_empty_search() -> None:
    with pytest.raises(df.FigureError, match="no section titled"):
        df.section_span(DOC, "## Nonexistent")


# --- locating the value ----------------------------------------------------


def test_the_value_span_covers_only_the_captured_group() -> None:
    figure = _figure()
    start, end = df.value_span(figure, DOC)
    assert DOC[start:end] == "85"
    assert df.stated_value(figure, DOC) == "85"


def test_scoping_to_a_section_ignores_an_identical_number_elsewhere() -> None:
    """`## History` states 70% for a past day. A whole-document search would
    find both and be right about neither."""
    figure = _figure(section=None, pattern=re.compile(r"(?P<value>\d+)%"))
    with pytest.raises(df.FigureError, match="claims match"):
        df.value_span(figure, DOC)


def test_a_pattern_matching_nothing_is_an_error_not_a_silent_pass() -> None:
    figure = _figure(pattern=re.compile(r"holds firmly at (?P<value>\d+)%"))
    with pytest.raises(df.FigureError, match="no claim matching"):
        df.value_span(figure, DOC)


def test_a_pattern_matching_twice_in_its_section_is_an_error() -> None:
    """Two matches means the stamp would have to choose, and choosing wrong
    rewrites a sentence nobody asked it to touch."""
    figure = _figure(pattern=re.compile(r"(?P<value>85)%"))
    with pytest.raises(df.FigureError, match="2 claims match"):
        df.value_span(figure, DOC)


# --- stamping --------------------------------------------------------------


def test_stamping_replaces_the_value_and_nothing_around_it() -> None:
    stamped = df.stamp_text(_figure(), DOC, "90")
    assert "The gate holds at 90% and nothing else here counts." in stamped
    assert "it also says 85% once" in stamped, "the subsection is not this figure's claim"
    assert "On some past day it was 70%." in stamped, "history must survive a stamp"
    assert len(stamped) == len(DOC)


def test_stamping_a_wider_value_keeps_the_rest_of_the_line() -> None:
    stamped = df.stamp_text(_figure(), DOC, "100")
    assert "The gate holds at 100% and nothing else here counts." in stamped


def test_stamping_refuses_when_the_claim_cannot_be_located() -> None:
    figure = _figure(pattern=re.compile(r"absent (?P<value>\d+)%"))
    with pytest.raises(df.FigureError):
        df.stamp_text(figure, DOC, "90")


# --- the manifest itself ---------------------------------------------------


def test_every_figure_has_a_unique_name() -> None:
    names = [figure.name for figure in df.FIGURES]
    assert len(names) == len(set(names)), names


@pytest.mark.parametrize("figure", df.FIGURES, ids=lambda f: f.name)
def test_every_figure_names_a_document_that_exists(figure: object) -> None:
    assert figure.path.is_file(), f"{figure.document} does not exist"


@pytest.mark.parametrize("figure", df.FIGURES, ids=lambda f: f.name)
def test_every_figure_captures_a_group_named_value(figure: object) -> None:
    assert "value" in figure.pattern.groupindex, (
        f"{figure.name}'s pattern has no (?P<value>...) group, so --write would "
        "have nothing to replace"
    )


@pytest.mark.parametrize("figure", df.FIGURES, ids=lambda f: f.name)
def test_every_figure_states_its_source_of_truth(figure: object) -> None:
    assert figure.source.strip(), f"{figure.name} does not say where its value comes from"


@pytest.mark.parametrize("figure", df.FIGURES, ids=lambda f: f.name)
def test_every_figure_locates_exactly_one_claim_in_the_live_document(figure: object) -> None:
    """The regression this catches: a doc reworded around a gated figure, leaving
    a manifest row that matches nothing and therefore checks nothing."""
    text = figure.path.read_text(encoding="utf-8")
    assert df.stated_value(figure, text)


# --- the file-backed derivers, against the live documents ------------------

_OFFLINE_DERIVERS = (
    df.coverage_floor_percent,
    df.coverage_scope,
    df.refresh_limit_default,
    df.mutation_kill_threshold,
    df.refresh_cadence_days,
)


@pytest.mark.parametrize(
    "figure",
    [f for f in df.FIGURES if f.derive in _OFFLINE_DERIVERS],
    ids=lambda f: f.name,
)
def test_the_live_documents_state_what_the_repo_derives(figure: object) -> None:
    text = figure.path.read_text(encoding="utf-8")
    stated, derived = df.stated_value(figure, text), figure.derive()
    assert stated == derived, (
        f"{figure.document} states {stated!r}; {figure.source} says {derived!r}. Run `make stamp`."
    )


def test_the_mutation_threshold_is_derived_by_inverting_the_survival_ceiling() -> None:
    """`scripts/mutation-gate.sh` enforces `cr-rate --fail-over 30`; the README
    states the kill floor. Reading the script's own "70%" prose would check a
    sentence against itself, so the deriver inverts the enforced number."""
    script = (REPO_ROOT / "scripts" / "mutation-gate.sh").read_text(encoding="utf-8")
    ceilings = {int(v) for v in re.findall(r"cr-rate[^\n]*--fail-over\s+(\d+)", script)}
    assert len(ceilings) == 1, ceilings
    assert df.mutation_kill_threshold() == str(100 - ceilings.pop())


def test_the_coverage_scope_is_every_measured_package() -> None:
    """`app` joined the coverage addopts on 2026-08-28 and DEFINITION_OF_DONE.md
    went on naming three packages. The scope is a claim like any other."""
    scope = df.coverage_scope()
    assert scope.startswith("`") and "/" in scope
    for package in ("pipeline", "recommender", "export"):
        assert f"`{package}`" in scope
