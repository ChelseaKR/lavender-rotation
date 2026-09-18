#!/usr/bin/env python3
"""Docs-figures: every figure the docs state about this repo, derived from the repo.

`scripts/check-readme-claims.py` (2026-08-04) closed one instance of a general
problem: README's "NNN tests at NN% coverage" sentence had drifted from 433 to a
real 501 with nothing to notice. Its own docstring, and `docs/ROADMAP.md`'s build
log for it, said what it was: the local fix, with "the M8 auto-stamp backlog item
is the systemic fix and **remains open**."

This is that item. The difference is not that more numbers are checked — it is
that a checked figure is now a *manifest row* rather than a script. Each
:class:`Figure` pairs one claim, located by a regex inside one named section of
one document, with the callable that re-derives it from the thing it describes.
Adding a gated figure is four lines in ``FIGURES``; nothing here knows what a
test count or a coverage floor *is*.

Two consequences worth stating, because they are the reason to prefer a manifest
over another bespoke guard:

* **It stamps, it does not only complain.** ``--write`` substitutes the derived
  value into the document. The old guard could only fail and ask a human to
  retype a number, which is the same hand-editing step that produced the drift.
* **A figure stated in several documents is one row per statement, one deriver.**
  The ``>=85%`` coverage floor is written in four places and lives in exactly one
  (``pyproject.toml``'s ``--cov-fail-under``). Raising the floor and updating
  three of the four is now a failing gate rather than a silent inconsistency.

**What is deliberately not gated.** A dated, point-in-time number inside a
historical record is not a claim about today, and rewriting one would falsify the
record. ``docs/ROADMAP.md``'s build-log addenda, ``docs/plans/*``,
``docs/USER-RESEARCH.md``'s persona quoting "108 tests @ 94%", ``CHANGELOG.md``,
and ``docs/ideation/*`` are all snapshots by design. Every row below therefore
names the *section* its claim lives in, so a figure can never be located by
accident somewhere the manifest did not mean; and a pattern that matches twice
inside its section is an error, never a first-match guess.

Run with no arguments to check (non-zero on drift, wired into ``make test``);
run with ``--write`` to stamp (``make stamp``).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class FigureError(RuntimeError):
    """The manifest and the document disagree about *where* a claim is.

    Distinct from drift on purpose. Drift means the number is stale and the fix
    is to stamp it. This means the sentence moved, was reworded, or now appears
    twice — and a stamp that guessed which occurrence to rewrite would be worse
    than no stamp at all.
    """


@dataclass(frozen=True)
class Figure:
    """One stated figure, and the thing that is allowed to decide its value."""

    #: Stable identifier, used in output and in the tests. Never derived from
    #: the document, so rewording a sentence does not rename a gate.
    name: str
    #: Repo-relative path of the document making the claim.
    document: str
    #: Heading whose section holds the claim, verbatim including its ``#``s.
    #: ``None`` means the whole document, which is only honest for a short file
    #: with no historical passages.
    section: str | None
    #: Must capture the value, and only the value, in a group named ``value``:
    #: that group's span is exactly what ``--write`` replaces.
    pattern: re.Pattern[str]
    #: Re-derives the value from the repo. Returns the string as it should read
    #: in the document, so a figure is not required to be an integer.
    derive: Callable[[], str]
    #: Human-readable source of truth, printed in every report line. A gate that
    #: cannot say where its answer came from is not reviewable.
    source: str

    @property
    def path(self) -> Path:
        return REPO_ROOT / self.document


@dataclass(frozen=True)
class Result:
    figure: Figure
    stated: str
    derived: str

    @property
    def ok(self) -> bool:
        return self.stated == self.derived


# --- locating a claim ------------------------------------------------------

_HEADING = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)


def section_span(text: str, heading: str) -> tuple[int, int]:
    """Character span of ``heading``'s body: after its own line, up to the next
    heading of the same or a shallower level (or end of document).

    Scoping by section is what keeps a live claim distinguishable from a dated
    one in the same file. ``docs/ROADMAP.md`` states a coverage floor in §7 as a
    current gate and quotes older test counts a few hundred lines above; only
    the first is a claim about today.
    """
    for match in _HEADING.finditer(text):
        if f"{match.group('hashes')} {match.group('title')}" != heading:
            continue
        level = len(match.group("hashes"))
        start = match.end()
        for later in _HEADING.finditer(text, start):
            if len(later.group("hashes")) <= level:
                return start, later.start()
        return start, len(text)
    raise FigureError(f"no section titled {heading!r}")


def value_span(figure: Figure, text: str) -> tuple[int, int]:
    """Span of the stated value inside ``text``, or :class:`FigureError`."""
    start, end = (0, len(text)) if figure.section is None else section_span(text, figure.section)
    matches = list(figure.pattern.finditer(text, start, end))
    where = figure.document + (f" {figure.section}" if figure.section else "")
    if not matches:
        raise FigureError(
            f"{figure.name}: no claim matching {figure.pattern.pattern!r} in {where}. "
            "The wording changed — update the manifest deliberately rather than "
            "leaving a gate that matches nothing and passes."
        )
    if len(matches) > 1:
        raise FigureError(
            f"{figure.name}: {len(matches)} claims match {figure.pattern.pattern!r} in "
            f"{where}. Narrow the pattern or the section; a stamp must not guess "
            "which occurrence you meant."
        )
    return matches[0].span("value")


def stated_value(figure: Figure, text: str) -> str:
    start, end = value_span(figure, text)
    return text[start:end]


def stamp_text(figure: Figure, text: str, value: str) -> str:
    """Return ``text`` with this figure's value replaced. Pure; no I/O."""
    start, end = value_span(figure, text)
    return text[:start] + value + text[end:]


# --- the sources of truth --------------------------------------------------


def collected_test_count() -> str:
    """How many tests pytest collects right now.

    ``--no-cov``: this repo's addopts always enable coverage, which is pointless
    for a collection pass and whose ``--cov-fail-under`` would make the
    subprocess exit non-zero even though collection succeeded.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-cov"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout
    match = re.search(r"^(?P<count>\d+) tests? collected", output, re.MULTILINE)
    if not match:
        raise FigureError(
            f"could not read a test count from `pytest --collect-only`:\n{output}{completed.stderr}"
        )
    return match.group("count")


def coverage_total_percent() -> str:
    """The coverage total for the run that just happened.

    Read from the ``coverage`` CLI against the fresh ``.coverage`` database
    rather than any committed file: ``docs/audits/coverage.xml`` is gitignored
    regenerable churn, and a figure derived from a stale artifact is the failure
    this whole script exists to stop.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "coverage", "report", "--format=total"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout.strip()
    if not output.isdigit():
        raise FigureError(
            "could not read a coverage total — did `pytest` run first in this "
            f"`make test` invocation to populate `.coverage`? Got: {output!r}"
        )
    return output


def _pytest_addopts() -> list[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [str(opt) for opt in data["tool"]["pytest"]["ini_options"]["addopts"]]


def coverage_floor_percent() -> str:
    """The merge-blocking coverage floor, from pytest's own ``--cov-fail-under``."""
    for opt in _pytest_addopts():
        if opt.startswith("--cov-fail-under="):
            return opt.split("=", 1)[1]
    raise FigureError("pyproject.toml's pytest addopts no longer set --cov-fail-under")


def coverage_scope() -> str:
    """The packages the coverage gate actually measures, rendered as the docs write them.

    Not a number, and that is the point: the same manifest gates a claim about
    *which* packages are covered. ``app`` was added to the addopts on 2026-08-28
    and `DEFINITION_OF_DONE.md` went on naming three packages.
    """
    packages = [opt.removeprefix("--cov=") for opt in _pytest_addopts() if opt.startswith("--cov=")]
    if not packages:
        raise FigureError("pyproject.toml's pytest addopts no longer set any --cov= package")
    return "/".join(f"`{name}`" for name in packages)


def refresh_limit_default() -> str:
    """``lavender refresh --limit``'s default, from the constant that sets it."""
    source = (REPO_ROOT / "pipeline" / "cli.py").read_text(encoding="utf-8")
    match = re.search(r"^DEFAULT_REFRESH_LIMIT = (?P<value>\d+)$", source, re.MULTILINE)
    if not match:
        raise FigureError("pipeline/cli.py no longer defines DEFAULT_REFRESH_LIMIT as a literal")
    return match.group("value")


def mutation_kill_threshold() -> str:
    """The mutation gate's kill floor, derived from what it actually enforces.

    ``scripts/mutation-gate.sh`` enforces a *survival* ceiling (``cr-rate
    --fail-over 30``); the docs state the *kill* floor. Deriving one from the
    other is the honest direction — reading the script's own "70%" prose would
    check a sentence against itself.
    """
    script = (REPO_ROOT / "scripts" / "mutation-gate.sh").read_text(encoding="utf-8")
    survival = {int(v) for v in re.findall(r"cr-rate[^\n]*--fail-over\s+(\d+)", script)}
    if not survival:
        raise FigureError("scripts/mutation-gate.sh no longer runs `cr-rate --fail-over N`")
    if len(survival) > 1:
        raise FigureError(
            f"scripts/mutation-gate.sh enforces several survival ceilings {sorted(survival)}; "
            "the docs state one kill threshold, so decide which is true"
        )
    return str(100 - survival.pop())


def refresh_cadence_days() -> str:
    """The periodic re-enrichment cadence (ADR 0013), from the constant both
    scheduler renderers express.

    A cadence stated in prose and expressed in a rendered launchd/cron entry is
    two copies of one decision. This is the row that makes the documented
    schedule and the installed one the same schedule.
    """
    source = (REPO_ROOT / "scripts" / "refresh_schedule.py").read_text(encoding="utf-8")
    match = re.search(r"^CADENCE_DAYS = (?P<value>\d+)$", source, re.MULTILINE)
    if not match:
        raise FigureError("scripts/refresh_schedule.py no longer defines CADENCE_DAYS as a literal")
    return match.group("value")


# --- the manifest ----------------------------------------------------------

FIGURES: tuple[Figure, ...] = (
    Figure(
        name="readme-test-count",
        document="README.md",
        section="## Project status",
        pattern=re.compile(r"(?P<value>\d+) tests at \d+% coverage"),
        derive=collected_test_count,
        source="`pytest --collect-only` (tests collected now)",
    ),
    Figure(
        name="readme-coverage-percent",
        document="README.md",
        section="## Project status",
        pattern=re.compile(r"\d+ tests at (?P<value>\d+)% coverage"),
        derive=coverage_total_percent,
        source="`coverage report --format=total` (this run's .coverage)",
    ),
    Figure(
        name="readme-refresh-limit",
        document="README.md",
        section="## Project status",
        pattern=re.compile(r"`--limit` \(default (?P<value>\d+)\)"),
        derive=refresh_limit_default,
        source="pipeline/cli.py::DEFAULT_REFRESH_LIMIT",
    ),
    Figure(
        name="readme-refresh-cadence-days",
        document="README.md",
        section="## Project status",
        pattern=re.compile(r"runs `make refresh` every (?P<value>\d+) days"),
        derive=refresh_cadence_days,
        source="scripts/refresh_schedule.py::CADENCE_DAYS (ADR 0013)",
    ),
    Figure(
        name="roadmap-refresh-cadence-days",
        document="docs/ROADMAP.md",
        section="## 11. Operations & sustainability",
        pattern=re.compile(r"executes it every (?P<value>\d+) days"),
        derive=refresh_cadence_days,
        source="scripts/refresh_schedule.py::CADENCE_DAYS (ADR 0013)",
    ),
    Figure(
        name="readme-mutation-kill-threshold",
        document="README.md",
        section="## Standards Conformance",
        pattern=re.compile(r"fails under (?P<value>\d+)% mutants killed"),
        derive=mutation_kill_threshold,
        source="scripts/mutation-gate.sh (`cr-rate --fail-over N`, inverted)",
    ),
    Figure(
        name="contributing-coverage-floor",
        document="CONTRIBUTING.md",
        section="## The one command that proves it: `make verify`",
        pattern=re.compile(r"\*\*≥ (?P<value>\d+)%\*\* coverage gate"),
        derive=coverage_floor_percent,
        source="pyproject.toml pytest addopts `--cov-fail-under`",
    ),
    Figure(
        name="contributing-checklist-coverage-floor",
        document="CONTRIBUTING.md",
        section="## Pull requests",
        pattern=re.compile(r"test ≥(?P<value>\d+)%"),
        derive=coverage_floor_percent,
        source="pyproject.toml pytest addopts `--cov-fail-under`",
    ),
    Figure(
        name="definition-of-done-coverage-floor",
        document="DEFINITION_OF_DONE.md",
        section=None,
        pattern=re.compile(r"tests \(≥(?P<value>\d+)% branch-aware coverage"),
        derive=coverage_floor_percent,
        source="pyproject.toml pytest addopts `--cov-fail-under`",
    ),
    Figure(
        name="definition-of-done-coverage-scope",
        document="DEFINITION_OF_DONE.md",
        section=None,
        pattern=re.compile(r"branch-aware coverage on (?P<value>(?:`[\w.-]+`/?)+)"),
        derive=coverage_scope,
        source="pyproject.toml pytest addopts `--cov=` packages",
    ),
    Figure(
        name="roadmap-coverage-floor",
        document="docs/ROADMAP.md",
        section="## 7. Quality attributes & metrics",
        pattern=re.compile(r"\| Coverage \| ≥ (?P<value>\d+)% / ≥ \d+% \|"),
        derive=coverage_floor_percent,
        source="pyproject.toml pytest addopts `--cov-fail-under`",
    ),
)


# --- check / stamp ---------------------------------------------------------


def evaluate(figures: Sequence[Figure] = FIGURES) -> list[Result]:
    """Read each stated figure and derive what it should be. Raises on FigureError."""
    texts: dict[str, str] = {}
    results = []
    for figure in figures:
        text = texts.setdefault(figure.document, figure.path.read_text(encoding="utf-8"))
        results.append(Result(figure, stated_value(figure, text), figure.derive()))
    return results


def stamp(results: Sequence[Result]) -> list[str]:
    """Write every drifted figure into its document. Returns the paths changed.

    Replacements within one document are applied right-to-left so the spans,
    all computed against the same snapshot, stay valid as earlier text shifts.
    """
    by_document: dict[str, list[Result]] = {}
    for result in results:
        if not result.ok:
            by_document.setdefault(result.figure.document, []).append(result)

    changed = []
    for document, drifted in by_document.items():
        path = REPO_ROOT / document
        text = path.read_text(encoding="utf-8")
        for result in sorted(drifted, key=lambda r: value_span(r.figure, text)[0], reverse=True):
            text = stamp_text(result.figure, text, result.derived)
        path.write_text(text, encoding="utf-8")
        changed.append(document)
    return changed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="stamp the derived value into each document instead of only reporting drift",
    )
    args = parser.parse_args(argv)

    try:
        results = evaluate()
    except FigureError as exc:
        print(f"docs-figures: {exc}", file=sys.stderr)
        return 1

    drifted = [r for r in results if not r.ok]
    if not drifted:
        print(f"docs-figures: all {len(results)} stated figures match what the repo derives — ok")
        return 0

    if args.write:
        changed = stamp(results)
        for result in drifted:
            print(
                f"docs-figures: stamped {result.figure.name} in {result.figure.document}: "
                f"{result.stated} -> {result.derived} ({result.figure.source})"
            )
        print(
            f"docs-figures: rewrote {', '.join(sorted(changed))} — "
            "review the diff before committing"
        )
        return 0

    print("docs-figures: stated figures have drifted from what the repo derives:", file=sys.stderr)
    for result in drifted:
        print(
            f"  - {result.figure.name}: {result.figure.document} states "
            f"{result.stated!r}; {result.figure.source} says {result.derived!r}",
            file=sys.stderr,
        )
    print("  Run `make stamp` to write the derived values in.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
