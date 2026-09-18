"""Ingest, enrichment, and the sourced-not-inferred identity resolver."""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

# REL-02: one source of the version. `pyproject.toml` declares it, the build records it in
# the installed distribution's metadata, and everything that needs to state it — the
# outbound User-Agent, most of all — reads it back from there rather than writing the
# number down a second time where it can drift.
try:
    __version__ = version("lavender-rotation")
except PackageNotFoundError:  # pragma: no cover - only in an uninstalled source tree
    __version__ = "0.0.0+unknown"
