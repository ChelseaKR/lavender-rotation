"""Where Lavender Rotation stores its local data (FIX-12 — operability: a
documented, stable data location instead of a cwd-relative ``data/`` folder).

Resolution order:

1. ``LAVENDER_DATA_DIR`` env var, if set to a non-empty value — always honoured,
   any OS. (``WAD_DATA_DIR`` is still read, deprecated, see below.)
2. Otherwise a platformdirs-style per-OS user-data directory, computed with
   the standard library only (no new dependency): ``~/Library/Application
   Support/lavender-rotation`` on macOS, ``%APPDATA%\\lavender-rotation`` on
   Windows, and ``$XDG_DATA_HOME/lavender-rotation`` (default
   ``~/.local/share/lavender-rotation``) elsewhere.

Both paths are absolute and independent of the process's current working
directory, so two shells started in different directories resolve to the
same cache (the bug this module fixes: ``pipeline/cache.py`` used to default
to the cwd-relative ``data/cache.db``).

**The rename migration.** This project was ``wad`` (women-artist-discovery)
until 2026-08-16. A rename that silently changed the data directory would
orphan an existing cache — on the maintainer's machine that was 95k scrobbles
and 450 enriched artists, hours of upstream fetching that nobody should have to
pay twice. So :func:`migrate_legacy_data_dir` moves the old directory to the new
name on first use, and the old env var keeps working. The move is a rename on
the same filesystem: fast, atomic, and reversible by moving it back. It happens
only when the new location does not exist yet, so it can never overwrite a real
cache, and never runs at all once the migration has happened.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("wad.paths")

_ENV_VAR = "LAVENDER_DATA_DIR"
#: Pre-rename env var. Read when the current one is unset so an operator's
#: existing shell profile, CI config, or script keeps working unchanged.
_LEGACY_ENV_VAR = "WAD_DATA_DIR"
_APP_NAME = "lavender-rotation"
_LEGACY_APP_NAME = "wad"
_DB_FILENAME = "cache.db"


def resolve_data_dir() -> Path:
    """Return the absolute directory this project stores its local data in.

    Honours ``LAVENDER_DATA_DIR`` when set to a non-empty (whitespace-stripped)
    value, then the deprecated ``WAD_DATA_DIR``; otherwise falls back to
    :func:`_default_data_dir`. Pure path resolution — does not touch the
    filesystem.
    """
    for var in (_ENV_VAR, _LEGACY_ENV_VAR):
        override = os.environ.get(var, "").strip()
        if override:
            return Path(override).expanduser().resolve()
    return _default_data_dir()


def _platform_base() -> Path:
    """The per-OS user-data base directory, keyed off ``sys.platform``."""
    if sys.platform == "darwin":
        return Path(os.path.expanduser("~/Library/Application Support"))
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        return Path(appdata) if appdata else Path(os.path.expanduser("~/AppData/Roaming"))
    xdg = os.environ.get("XDG_DATA_HOME")
    return Path(xdg) if xdg else Path(os.path.expanduser("~/.local/share"))


def _default_data_dir() -> Path:
    """The platformdirs-style default."""
    return (_platform_base() / _APP_NAME).resolve()


def legacy_data_dir() -> Path:
    """Where the pre-rename build kept its data. Path only; may not exist."""
    return (_platform_base() / _LEGACY_APP_NAME).resolve()


def migrate_legacy_data_dir() -> bool:
    """Move a pre-rename data directory to the new name. Returns whether it moved.

    Deliberately conservative, because the thing being moved is hours of
    rate-limited upstream fetching that cannot be reconstructed quickly:

    * Only when the **new** directory does not exist. An existing new-name
      directory is the source of truth and is never overwritten or merged.
    * Only when the **old** one does, and is a real directory.
    * Only when neither is overridden by an env var — an operator who named a
      path explicitly gets exactly that path and no surprise moves.
    * ``os.replace``-style rename on one filesystem: atomic, and undone by
      moving the directory back.

    Any ``OSError`` is swallowed after logging: a failed migration must leave
    the caller with a working (if empty) new directory, not a crash on startup.
    """
    if any(os.environ.get(var, "").strip() for var in (_ENV_VAR, _LEGACY_ENV_VAR)):
        return False
    new, old = _default_data_dir(), legacy_data_dir()
    if new.exists() or not old.is_dir():
        return False
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
    except OSError:
        log.warning("stage=paths event=migration_failed")
        return False
    log.info("stage=paths event=migrated_data_dir")
    return True


def default_db_path() -> Path:
    """Return the resolved cache database path, creating its parent directory.

    Callers that only need the *path* (e.g. display in ``lavender doctor``) can
    use :func:`resolve_data_dir` instead to avoid the filesystem write.
    """
    migrate_legacy_data_dir()
    data_dir = resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / _DB_FILENAME
