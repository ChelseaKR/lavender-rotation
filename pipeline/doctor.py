"""``wad doctor`` diagnostics (FIX-12 — operability pass).

All the actual checking logic lives here, pure and unit-testable; the CLI
(``pipeline/cli.py``, excluded from the coverage gate) is thin argparse +
print glue over :func:`run_diagnostics`.

Checks:

* **env** — whether each API-related environment variable is *present*.
  Informational only (demo mode needs none of them): never hard-fails, and
  never includes the value, only presence/absence.
* **cache** — the resolved data directory + cache path, whether the cache
  file opens cleanly, whether its ``PRAGMA user_version`` matches
  :data:`pipeline.cache.CACHE_SCHEMA_VERSION`, and its on-disk size. Hard
  checks: a cache that won't open or is on the wrong schema version is
  exactly the kind of silent failure this command exists to surface. Size is
  informational — it's the one signal `wad doctor` gives an operator for
  *when* to reach for `wad refresh --ttl-days`, since nothing else in the
  CLI reports cache footprint.
* **upstream** — opt-in only (``--check-upstream``); pings the four external
  APIs this project talks to. Never runs by default, and its own failures are
  never hard (a bad network shouldn't make ``wad doctor`` non-zero on a
  perfectly healthy local install).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from pipeline.cache import CACHE_SCHEMA_VERSION, Cache, CacheSchemaError
from pipeline.paths import default_db_path, resolve_data_dir

# Environment variables the pipeline reads. None are strictly required for
# demo mode; WAD_LASTFM_API_KEY enables live ingest, and the WAD_SPOTIFY_* /
# WAD_TIDAL_* sets each enable one playlist-export provider. A new exporter
# belongs here so `wad doctor` can tell the operator what is configured.
# Report presence only — never the value.
ENV_KEYS: tuple[str, ...] = (
    "WAD_LASTFM_API_KEY",
    "WAD_SPOTIFY_CLIENT_ID",
    "WAD_SPOTIFY_CLIENT_SECRET",
    "WAD_SPOTIFY_REDIRECT_URI",
    "WAD_TIDAL_CLIENT_ID",
    "WAD_TIDAL_CLIENT_SECRET",
    "WAD_TIDAL_REDIRECT_URI",
)

# (human label, host) — used only by the opt-in upstream reachability check.
UPSTREAM_APIS: tuple[tuple[str, str], ...] = (
    ("Last.fm", "https://ws.audioscrobbler.com/2.0/"),
    ("MusicBrainz", "https://musicbrainz.org/ws/2/"),
    ("Wikidata", "https://www.wikidata.org/wiki/Special:EntityData"),
    ("Discogs", "https://api.discogs.com/"),
)


@dataclass(frozen=True)
class Check:
    """One diagnostic result.

    ``hard`` marks whether a failure should make ``wad doctor`` exit non-zero;
    informational checks (env presence, opt-in upstream reachability) are
    reported but never fail the run on their own.
    """

    name: str
    passed: bool
    detail: str
    hard: bool = True


@dataclass(frozen=True)
class DoctorReport:
    """The full set of checks from one ``run_diagnostics()`` call."""

    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        """True unless a *hard* check failed (soft/informational checks never fail it)."""
        return all(c.passed for c in self.checks if c.hard)


def _check_env_keys() -> list[Check]:
    checks: list[Check] = []
    for key in ENV_KEYS:
        present = bool(os.environ.get(key, "").strip())
        checks.append(
            Check(
                name=f"env:{key}",
                passed=True,
                detail="present" if present else "missing (only needed for live/export modes)",
                hard=False,
            )
        )
    return checks


def _format_bytes(n: int) -> str:
    """Human-readable byte count, e.g. ``4.0 KiB``. Binary (1024) units, capped at GiB."""
    size = float(n)
    unit = "B"
    for next_unit in ("KiB", "MiB", "GiB"):
        if size < 1024:
            break
        size /= 1024
        unit = next_unit
    return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"


def _check_cache() -> list[Check]:
    checks: list[Check] = []
    try:
        data_dir = resolve_data_dir()
    except OSError as exc:  # pragma: no cover - resolve_data_dir is pure path math
        checks.append(Check("data_dir", False, f"cannot resolve data dir: {exc}"))
        return checks
    checks.append(Check("data_dir", True, str(data_dir), hard=False))

    try:
        db_path = default_db_path()
    except OSError as exc:
        checks.append(Check("cache_path", False, f"cannot create data dir: {exc}"))
        return checks
    checks.append(Check("cache_path", True, str(db_path), hard=False))

    try:
        cache = Cache(db_path)
    except CacheSchemaError as exc:
        checks.append(Check("cache_schema_version", False, str(exc)))
        return checks
    except (sqlite3.Error, OSError) as exc:
        checks.append(Check("cache_readable", False, f"cannot open cache: {exc}"))
        return checks

    try:
        (version,) = cache.conn.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error as exc:  # pragma: no cover - defensive, sqlite3 core rarely fails here
        checks.append(Check("cache_readable", False, f"cannot read cache: {exc}"))
        return checks
    finally:
        cache.close()

    checks.append(Check("cache_readable", True, f"opened {db_path}"))
    checks.append(
        Check(
            "cache_schema_version",
            version == CACHE_SCHEMA_VERSION,
            f"user_version={version} (expected {CACHE_SCHEMA_VERSION})",
        )
    )
    try:
        size_bytes = db_path.stat().st_size
    except OSError as exc:  # pragma: no cover - defensive; the open above already proved it exists
        checks.append(Check("cache_size", False, f"cannot stat cache file: {exc}", hard=False))
    else:
        checks.append(Check("cache_size", True, _format_bytes(size_bytes), hard=False))
    return checks


def _check_upstream_reachability(timeout: float = 5.0) -> list[Check]:
    """Opt-in network reachability probe. Only ever runs when explicitly requested."""
    # Deliberately a local import: this is the only function in pipeline/ that
    # touches the network, and only when explicitly opted into.
    import requests

    checks: list[Check] = []
    for label, url in UPSTREAM_APIS:
        try:
            requests.head(url, timeout=timeout)
        except requests.RequestException as exc:
            checks.append(Check(f"upstream:{label}", False, f"unreachable: {exc}", hard=False))
        else:
            checks.append(Check(f"upstream:{label}", True, f"reachable ({url})", hard=False))
    return checks


def run_diagnostics(check_upstream: bool = False) -> DoctorReport:
    """Run every doctor check and return a report.

    ``check_upstream`` is opt-in and off by default — it is the only check
    that touches the network, per FIX-07's no-egress-by-default posture.
    """
    checks: list[Check] = [*_check_env_keys(), *_check_cache()]
    if check_upstream:
        checks.extend(_check_upstream_reachability())
    return DoctorReport(checks=tuple(checks))
