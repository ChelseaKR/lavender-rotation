"""Unit tests for scripts/check-readme-claims.py's pure logic (regex
extraction + comparison). The script's filename has a hyphen, so it is loaded
by path via importlib rather than a normal package import; the module is
never executed as `__main__` here, so no subprocess/file I/O happens as a
side effect of import.

The subprocess-driven helpers (`_actual_test_count`, `_actual_coverage_pct`)
are deliberately not unit-tested here: they are thin I/O wrappers around
`pytest --collect-only` and `coverage report`, exercised for real every time
`make test` runs this script as its own gate step — the same convention the
repo already applies to its other doc/claim-checking scripts (e.g.
`scripts/writeup-check.py`), which run against the live repo rather than
synthetic fixtures.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check-readme-claims.py"


def _load_check_readme_claims() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_readme_claims", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_readme_claims = _load_check_readme_claims()


def test_parse_claim_extracts_count_and_percentage() -> None:
    text = "strict typing, 433 tests at 97% coverage, dependency and secret scans"
    assert check_readme_claims.parse_claim(text) == (433, 97)


def test_parse_claim_is_none_when_wording_is_absent() -> None:
    assert check_readme_claims.parse_claim("no such claim in this text") is None


def test_parse_claim_ignores_unrelated_numbers() -> None:
    text = "v0.1.0 shipped on day 30; separately, 10 tests at 5% coverage, more text"
    assert check_readme_claims.parse_claim(text) == (10, 5)


def test_find_drift_is_empty_when_numbers_match() -> None:
    assert check_readme_claims.find_drift(433, 97, 433, 97) == []


def test_find_drift_reports_stale_test_count_only() -> None:
    problems = check_readme_claims.find_drift(433, 97, 493, 97)
    assert len(problems) == 1
    assert "433" in problems[0]
    assert "493" in problems[0]


def test_find_drift_reports_stale_coverage_percentage_only() -> None:
    problems = check_readme_claims.find_drift(433, 97, 433, 96)
    assert len(problems) == 1
    assert "97%" in problems[0]
    assert "96%" in problems[0]


def test_find_drift_reports_both_when_both_are_stale() -> None:
    problems = check_readme_claims.find_drift(433, 97, 500, 90)
    assert len(problems) == 2


def test_readme_currently_contains_a_well_formed_claim() -> None:
    # A narrow sanity check that the live README still matches the pattern
    # this script depends on — if this ever fails, the wording changed and
    # the CLAIM regex (or the README) needs a deliberate update, not a silent
    # gate no-op.
    readme_text = check_readme_claims.README.read_text(encoding="utf-8")
    assert check_readme_claims.parse_claim(readme_text) is not None
