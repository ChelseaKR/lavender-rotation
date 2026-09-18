"""The Python floor is one decision, written down in a dozen places.

`scripts/docs_figures.py` gates the figures that appear *once* per document and
have a single derivable value. This file holds the other shape: a claim repeated
across prose, `pyproject.toml`'s own comments, and the CI matrix, where the check
is "every statement agrees with the one declaration" rather than "this span says
N". The unit tests for the figures manifest live in `tests/test_doc_figures.py`.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

# --- The supported Python version, stated in eleven places ------------------
#
# ADR 0002 set the floor to >=3.10; ADR 0004 superseded it with >=3.12. The
# `requires-python` key moved and the CI matrix moved; the prose did not. On
# 2026-08-28 the Makefile's audit-waiver rationale still said "with the floor
# now >=3.10", `pyproject.toml`'s own comments said the same in two places and
# claimed "we type-check our own code against py310" twelve lines below
# `python_version = "3.12"`, and `docs/PROJECT-SCOPE.md` and
# `docs/DOCUMENTATION-AUDIT.md` both declared the package as ">=3.10".
#
# A repo whose whole pitch is "every claim is checkable" should not need a
# human to notice that. This derives the floor from `pyproject.toml` and holds
# the prose to it.

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Any doc sentence declaring the Python this package needs. Narrow on purpose:
#: a *historical* statement inside a dated record ("as of the 3.10 migration")
#: is not a claim about today, and rewriting those would falsify the record.
FLOOR_CLAIM = re.compile(
    r"for Python `?(>=\s*3\.\d+)`?|package `lavender-rotation` \(?(>=\s*3\.\d+)"
)

DOCS_STATING_A_FLOOR = ("docs/PROJECT-SCOPE.md", "docs/DOCUMENTATION-AUDIT.md")


def _declared_floor() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["requires-python"]).replace(" ", "")


def test_the_python_floor_is_declared_once_and_the_docs_match_it() -> None:
    floor = _declared_floor()
    assert floor.startswith(">="), floor
    for rel in DOCS_STATING_A_FLOOR:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        claims = {(m.group(1) or m.group(2)).replace(" ", "") for m in FLOOR_CLAIM.finditer(text)}
        assert claims, f"{rel} no longer states a Python floor; update this test or the doc"
        assert claims == {floor}, (
            f"{rel} declares Python {sorted(claims)} while pyproject.toml requires {floor}"
        )


def test_the_ruff_and_mypy_targets_match_the_declared_floor() -> None:
    """The two tool targets are the same decision written twice more."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    floor = _declared_floor().removeprefix(">=")
    major, minor = floor.split(".")[:2]
    assert data["tool"]["ruff"]["target-version"] == f"py{major}{minor}"
    assert data["tool"]["mypy"]["python_version"] == f"{major}.{minor}"


def test_the_ci_matrix_contains_no_runtime_below_the_floor() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    matrix = re.search(r"python-version:\s*\[(?P<versions>[^\]]+)\]", workflow)
    assert matrix is not None, "the CI matrix is no longer a plain literal list"
    versions = [v.strip().strip("\"'") for v in matrix.group("versions").split(",")]
    floor = tuple(int(part) for part in _declared_floor().removeprefix(">=").split("."))
    for version in versions:
        parsed = tuple(int(part) for part in version.split("."))
        assert parsed >= floor, (
            f"CI runs Python {version}, below the declared floor {_declared_floor()}"
        )
