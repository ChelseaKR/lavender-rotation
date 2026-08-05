#!/usr/bin/env python3
"""Docs-currency guard: README.md's "Project status" section states, in the
present tense, how many tests exist and what they cover ("... 433 tests at
97% coverage, dependency and secret scans, ..."). Nothing regenerated that
sentence, so it silently drifted as the suite grew — PR #49 (mutation-testing
gate on identity.py + rerank.py) flagged this exact gap in its own
description: "the README build-blockquote test count is not bumped here ...
the M8 auto-stamp backlog item is the systemic fix and remains open."

This is that fix. It re-derives both numbers independently — a fresh
``pytest --collect-only`` count and the ``coverage`` CLI's own total — and
fails loudly if either has drifted from what README.md currently claims. Same
"claims must be regenerable, never hand-typed and stale" discipline
`scripts/writeup-check.py` already applies to docs/writeup/methods.md,
applied here to the README's own gate summary.

Run as the last step of `make test` (stage 3), right after pytest has
populated `.coverage` for this run — the coverage figure must reflect the
run that just happened, not a stale artifact. `docs/audits/coverage.xml` is
itself gitignored regenerable churn (see .gitignore), so this deliberately
reads coverage via the `coverage` CLI against the fresh `.coverage` db
instead of trusting any committed file.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"

# Matches the exact wording of README's "## Project status" paragraph:
#   "... strict typing, 433 tests at 97% coverage, dependency and ..."
CLAIM = re.compile(r"(?P<count>\d+) tests at (?P<pct>\d+)% coverage")

# pytest's own collection summary, e.g. "493 tests collected in 0.29s"
# (singular "1 test collected" for the degenerate one-test case).
COLLECTED = re.compile(r"^(?P<count>\d+) tests? collected", re.MULTILINE)


def parse_claim(text: str) -> tuple[int, int] | None:
    """Extract (claimed_test_count, claimed_coverage_pct) from README text."""
    match = CLAIM.search(text)
    if not match:
        return None
    return int(match.group("count")), int(match.group("pct"))


def find_drift(
    claimed_count: int, claimed_pct: int, actual_count: int, actual_pct: int
) -> list[str]:
    """Pure comparison — returns a human-readable problem per mismatch."""
    problems = []
    if claimed_count != actual_count:
        problems.append(
            f"README claims {claimed_count} tests; the suite currently collects {actual_count}."
        )
    if claimed_pct != actual_pct:
        problems.append(
            f"README claims {claimed_pct}% coverage; `coverage report "
            f"--format=total` currently reports {actual_pct}%."
        )
    return problems


def _actual_test_count() -> int:
    # --no-cov: this repo's pytest addopts always enable coverage collection,
    # which is pointless (and noisy) for a plain collection pass, and its
    # --cov-fail-under would make this subprocess exit non-zero even though
    # collection itself succeeded.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-cov"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    match = COLLECTED.search(result.stdout)
    if not match:
        print(
            "check-readme-claims: could not determine the actual test count "
            "from `pytest --collect-only`. Output was:\n" + result.stdout + result.stderr,
            file=sys.stderr,
        )
        raise SystemExit(1)
    return int(match.group("count"))


def _actual_coverage_pct() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "coverage", "report", "--format=total"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout.strip()
    if not output.isdigit():
        print(
            "check-readme-claims: could not read a coverage total — did "
            "`pytest` run first in this `make test` invocation to populate "
            "`.coverage`? `coverage report --format=total` said:\n" + output + "\n" + result.stderr,
            file=sys.stderr,
        )
        raise SystemExit(1)
    return int(output)


def main() -> int:
    if not README.exists():
        print(f"check-readme-claims: {README} is missing", file=sys.stderr)
        return 1

    claim = parse_claim(README.read_text(encoding="utf-8"))
    if claim is None:
        print(
            f"check-readme-claims: {README} no longer contains an "
            "'N tests at M% coverage' claim in the expected form — update "
            "this script's CLAIM regex if the wording changed intentionally.",
            file=sys.stderr,
        )
        return 1
    claimed_count, claimed_pct = claim

    actual_count = _actual_test_count()
    actual_pct = _actual_coverage_pct()

    problems = find_drift(claimed_count, claimed_pct, actual_count, actual_pct)
    if problems:
        print(
            "check-readme-claims: README.md's Project status line has drifted:",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "  Update the 'NNN tests at NN% coverage' wording in README.md's "
            "'## Project status' section to match.",
            file=sys.stderr,
        )
        return 1

    print(
        f"check-readme-claims: README's {actual_count} tests / {actual_pct}% "
        "coverage claim matches — ok"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
