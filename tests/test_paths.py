"""FIX-12: LAVENDER_DATA_DIR override + the platformdirs-style default."""

from __future__ import annotations

from pathlib import Path

from pipeline import paths


def test_env_override_is_honored(monkeypatch, tmp_path) -> None:
    target = tmp_path / "custom-lavender-data"
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(target))
    assert paths.resolve_data_dir() == target.resolve()


def test_env_override_expands_user(monkeypatch) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", "~")
    resolved = paths.resolve_data_dir()
    assert resolved == Path.home().resolve()
    assert resolved.is_absolute()


def test_blank_env_override_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", "   ")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert paths.resolve_data_dir() == paths._default_data_dir()


def test_unset_env_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.delenv("LAVENDER_DATA_DIR", raising=False)
    assert paths.resolve_data_dir() == paths._default_data_dir()


def test_default_dir_is_stable_and_absolute(monkeypatch) -> None:
    monkeypatch.delenv("LAVENDER_DATA_DIR", raising=False)
    first = paths.resolve_data_dir()
    second = paths.resolve_data_dir()
    assert first == second
    assert first.is_absolute()
    assert first.name == "lavender-rotation"


def test_default_dir_is_cwd_independent(monkeypatch, tmp_path) -> None:
    """Two 'shells' in different working directories must resolve the same path."""
    monkeypatch.delenv("LAVENDER_DATA_DIR", raising=False)
    here = tmp_path / "here"
    there = tmp_path / "there"
    here.mkdir()
    there.mkdir()

    monkeypatch.chdir(here)
    from_here = paths.resolve_data_dir()

    monkeypatch.chdir(there)
    from_there = paths.resolve_data_dir()

    assert from_here == from_there


def test_env_override_is_also_cwd_independent(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path / "shared"))
    monkeypatch.chdir(tmp_path)
    a = paths.resolve_data_dir()
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    b = paths.resolve_data_dir()
    assert a == b


def test_default_db_path_creates_parent_and_is_named_cache_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path / "data"))
    db_path = paths.default_db_path()
    assert db_path == (tmp_path / "data" / "cache.db").resolve()
    assert db_path.parent.is_dir()


def test_default_data_dir_platform_darwin(monkeypatch) -> None:
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    resolved = paths._default_data_dir()
    assert resolved.parts[-2:] == ("Application Support", "lavender-rotation")


def test_default_data_dir_platform_linux_uses_xdg(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    resolved = paths._default_data_dir()
    assert resolved == (tmp_path / "xdg" / "lavender-rotation").resolve()


def test_default_data_dir_platform_linux_falls_back_without_xdg(monkeypatch) -> None:
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    resolved = paths._default_data_dir()
    assert resolved.parts[-3:] == (".local", "share", "lavender-rotation")


def test_default_data_dir_platform_windows_uses_appdata(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    resolved = paths._default_data_dir()
    assert resolved == (tmp_path / "Roaming" / "lavender-rotation").resolve()


# --- the rename migration (ADR 0012) ---------------------------------------


def _no_env(monkeypatch) -> None:
    """Both data-dir env vars unset, so the migration is allowed to run."""
    monkeypatch.delenv("LAVENDER_DATA_DIR", raising=False)
    monkeypatch.delenv("WAD_DATA_DIR", raising=False)


def test_a_pre_rename_cache_is_moved_not_abandoned(monkeypatch, tmp_path) -> None:
    """The whole point: hours of rate-limited fetching must survive a rename."""
    _no_env(monkeypatch)
    monkeypatch.setattr(paths, "_platform_base", lambda: tmp_path)
    legacy = tmp_path / "wad"
    legacy.mkdir()
    (legacy / "cache.db").write_text("scrobbles")

    assert paths.migrate_legacy_data_dir() is True
    assert (tmp_path / "lavender-rotation" / "cache.db").read_text() == "scrobbles"
    assert not legacy.exists()


def test_an_existing_new_cache_is_never_overwritten(monkeypatch, tmp_path) -> None:
    """The new directory is the source of truth; a stale old one is left alone."""
    _no_env(monkeypatch)
    monkeypatch.setattr(paths, "_platform_base", lambda: tmp_path)
    (tmp_path / "wad").mkdir()
    (tmp_path / "wad" / "cache.db").write_text("old")
    (tmp_path / "lavender-rotation").mkdir()
    (tmp_path / "lavender-rotation" / "cache.db").write_text("current")

    assert paths.migrate_legacy_data_dir() is False
    assert (tmp_path / "lavender-rotation" / "cache.db").read_text() == "current"
    assert (tmp_path / "wad" / "cache.db").read_text() == "old", "left for the operator"


def test_an_explicit_data_dir_is_never_second_guessed(monkeypatch, tmp_path) -> None:
    """Naming a path explicitly means that path, with no surprise moves."""
    monkeypatch.setattr(paths, "_platform_base", lambda: tmp_path)
    (tmp_path / "wad").mkdir()
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path / "explicit"))

    assert paths.migrate_legacy_data_dir() is False
    assert (tmp_path / "wad").exists()


def test_the_migration_is_a_no_op_when_there_is_nothing_to_move(monkeypatch, tmp_path) -> None:
    _no_env(monkeypatch)
    monkeypatch.setattr(paths, "_platform_base", lambda: tmp_path)
    assert paths.migrate_legacy_data_dir() is False


def test_the_deprecated_env_var_still_works(monkeypatch, tmp_path) -> None:
    """An operator's existing shell profile or CI config keeps working."""
    monkeypatch.delenv("LAVENDER_DATA_DIR", raising=False)
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path / "legacy-override"))
    assert paths.resolve_data_dir() == (tmp_path / "legacy-override").resolve()


def test_the_current_env_var_wins_over_the_deprecated_one(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path / "old"))
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path / "new"))
    assert paths.resolve_data_dir() == (tmp_path / "new").resolve()


def test_a_failed_move_degrades_to_a_working_empty_directory(monkeypatch, tmp_path) -> None:
    """A migration that cannot complete must not crash the process on startup."""
    _no_env(monkeypatch)
    monkeypatch.setattr(paths, "_platform_base", lambda: tmp_path)
    (tmp_path / "wad").mkdir()

    def boom(self, target):  # type: ignore[no-untyped-def]
        raise OSError("cross-device link")

    monkeypatch.setattr(Path, "rename", boom)
    assert paths.migrate_legacy_data_dir() is False
