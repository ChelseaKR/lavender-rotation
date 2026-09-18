"""The README's *capability* claims, checked against what a shipped surface can reach.

The structural gates in this repo check that documented commands and flags exist. They
cannot catch a claim that names a real command and then describes a capability that
command does not have. Two such claims stood in the README: `lavender export` was
described as pushing to Spotify or TIDAL (it renders a local file and takes no
destination flag), and `lavender ingest` was called the only command that reaches
upstream (`refresh --user` and `doctor --check-upstream` reach it too).

So this module derives the answer from the import graph rather than from prose. An
export adapter is *reachable* only if a shipped entrypoint transitively imports it;
`export/tidal.py` is complete, tested, and imported by nothing but tests, which is a
true and publishable state, but not one the README may describe as a working push.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("pipeline", "recommender", "app", "export")

# The surfaces a user can actually invoke: the CLI, and the Streamlit dashboard.
CLI = "pipeline.cli"
DASHBOARD = "app.dashboard"

# While this holds, the README has to say so; if TIDAL is ever wired up, the sentence
# has to go. The gate below enforces both directions.
TIDAL_UNREACHABLE_NOTE = "no shipped surface imports it yet"


def _module_name(path: Path) -> str:
    relative = path.relative_to(REPO).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _source_modules() -> dict[str, Path]:
    modules: dict[str, Path] = {}
    for root in SOURCE_ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            modules[_module_name(path)] = path
    return modules


def _imports(path: Path, known: set[str]) -> set[str]:
    """Intra-repo modules imported by ``path``, resolved against ``known``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in known:
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module in known:
                found.add(node.module)
            # `from export import models` names a submodule, not an attribute.
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                if candidate in known:
                    found.add(candidate)
    return found


def _reachable_from(entrypoint: str) -> set[str]:
    modules = _source_modules()
    assert entrypoint in modules, f"{entrypoint} is not a module in this repo"
    seen: set[str] = set()
    queue = [entrypoint]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(_imports(modules[current], set(modules)) - seen)
    return seen


def test_the_cli_reaches_no_provider_push_adapter() -> None:
    """`lavender export` renders a local file; no provider adapter is wired into it."""
    reachable = _reachable_from(CLI)
    assert "export.tracklist" in reachable, "the CLI should still render portable formats"
    for adapter in ("export.spotify", "export.tidal"):
        assert adapter not in reachable, (
            f"{adapter} is now reachable from {CLI}. The README says the CLI takes no "
            f"destination flag and pushes to no provider; update it before shipping this."
        )


def test_spotify_push_ships_in_the_dashboard_only() -> None:
    reachable = _reachable_from(DASHBOARD)
    assert "export.spotify" in reachable, (
        "the README says Spotify push ships in the Streamlit dashboard; it no longer does"
    )
    assert "export.spotify" not in _reachable_from(CLI)


def test_tidal_is_unreachable_and_the_readme_says_so() -> None:
    """`export/tidal.py` is complete and tested but imported by nothing that ships."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    reachable = _reachable_from(CLI) | _reachable_from(DASHBOARD)
    if "export.tidal" in reachable:
        assert TIDAL_UNREACHABLE_NOTE not in readme, (
            "TIDAL is now reachable from a shipped surface, but the README still says it "
            f"is not. Remove {TIDAL_UNREACHABLE_NOTE!r} and describe the working path."
        )
    else:
        assert TIDAL_UNREACHABLE_NOTE in readme, (
            "export/tidal.py is imported by no shipped surface, so the README must say so. "
            f"Expected the phrase {TIDAL_UNREACHABLE_NOTE!r}."
        )


def test_no_source_claims_to_be_the_only_network_path_in_pipeline() -> None:
    """More than one pipeline module is network-allowed, so no comment may claim otherwise.

    `pipeline/doctor.py` carried "this is the only function in pipeline/ that touches the
    network" while `lastfm.py` and `http.py` both call `requests`, and all three sit in
    the `NETWORK_ALLOWED` allowlist that `tests/test_privacy.py` already gates.
    """
    from tests.test_privacy import NETWORK_ALLOWED

    pipeline_egress = {path for path in NETWORK_ALLOWED if path.startswith("pipeline/")}
    assert len(pipeline_egress) > 1, "allowlist changed; revisit the claim this gate guards"
    for path in sorted((REPO / "pipeline").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "only function in pipeline/ that" not in text, (
            f"{path.name} claims to be the only such function in pipeline/, but "
            f"{sorted(pipeline_egress)} are all sanctioned egress paths."
        )
