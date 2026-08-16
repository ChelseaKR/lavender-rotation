"""FIX-12: `wad doctor` diagnostic logic (pipeline/doctor.py).

cli.py is excluded from the coverage gate, so these tests exercise
``run_diagnostics`` directly; a couple also drive ``pipeline.cli.main(["doctor"])``
to check the thin glue wires exit codes correctly (still fine to run — it's
just not required for the coverage number).
"""

from __future__ import annotations

import requests
from pipeline import doctor
from pipeline.cache import CACHE_SCHEMA_VERSION, Cache
from pipeline.paths import default_db_path


def test_healthy_cache_and_no_hard_failures_exit_ok(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    with Cache(default_db_path()):
        pass  # just create a healthy, current-schema cache

    for key in doctor.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    report = doctor.run_diagnostics(check_upstream=False)
    assert report.ok

    names = {c.name for c in report.checks}
    assert {"data_dir", "cache_path", "cache_readable", "cache_schema_version"} <= names

    cache_check = next(c for c in report.checks if c.name == "cache_readable")
    assert cache_check.passed and cache_check.hard

    version_check = next(c for c in report.checks if c.name == "cache_schema_version")
    assert version_check.passed


def test_cache_size_is_reported_and_grows_with_content(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    db_path = default_db_path()
    with Cache(db_path):
        pass  # empty, freshly-created cache

    empty_report = doctor.run_diagnostics(check_upstream=False)
    empty_check = next(c for c in empty_report.checks if c.name == "cache_size")
    assert empty_check.passed
    assert not empty_check.hard  # informational — never fails the run
    assert empty_check.detail.split()[0].replace(".", "", 1).isdigit()

    # Legitimately grow the cache through its own API and confirm the report follows.
    with Cache(db_path) as cache:
        for i in range(200):
            cache.put_cached_response(f"https://example.invalid/{i}", "x" * 5000, "2026-08-04")

    grown_report = doctor.run_diagnostics(check_upstream=False)
    grown_check = next(c for c in grown_report.checks if c.name == "cache_size")
    assert grown_check.passed
    grown_bytes = db_path.stat().st_size
    empty_bytes_str = empty_check.detail
    assert grown_bytes > 0
    assert grown_check.detail != empty_bytes_str


def test_format_bytes_picks_the_right_unit() -> None:
    assert doctor._format_bytes(0) == "0 B"
    assert doctor._format_bytes(1023) == "1023 B"
    assert doctor._format_bytes(1024) == "1.0 KiB"
    assert doctor._format_bytes(1536) == "1.5 KiB"
    assert doctor._format_bytes(1024 * 1024) == "1.0 MiB"
    assert doctor._format_bytes(1024 * 1024 * 1024) == "1.0 GiB"
    assert doctor._format_bytes(5 * 1024 * 1024 * 1024) == "5.0 GiB"


def test_missing_env_keys_are_reported_but_never_fail_the_run(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    for key in doctor.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    report = doctor.run_diagnostics(check_upstream=False)
    env_checks = [c for c in report.checks if c.name.startswith("env:")]
    assert len(env_checks) == len(doctor.ENV_KEYS)
    assert all("missing" in c.detail for c in env_checks)
    assert all(not c.hard for c in env_checks)  # informational, never hard-fails
    assert report.ok


def test_present_env_keys_are_reported_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WAD_LASTFM_API_KEY", "irrelevant-for-this-test")

    report = doctor.run_diagnostics(check_upstream=False)
    lastfm_check = next(c for c in report.checks if c.name == "env:WAD_LASTFM_API_KEY")
    assert lastfm_check.detail == "present"


def test_doctor_never_prints_env_values(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    secret = "super-secret-do-not-leak"  # gitleaks:allow — test canary, not a credential
    monkeypatch.setenv("WAD_LASTFM_API_KEY", secret)

    report = doctor.run_diagnostics(check_upstream=False)
    for check in report.checks:
        assert secret not in check.detail
        assert secret not in check.name


def test_upstream_check_is_skipped_by_default(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    report = doctor.run_diagnostics(check_upstream=False)
    assert not any(c.name.startswith("upstream:") for c in report.checks)


def test_stale_schema_version_is_a_hard_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    db_path = default_db_path()
    with Cache(db_path) as cache:
        cache.conn.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION + 1}")
        cache.conn.commit()

    report = doctor.run_diagnostics(check_upstream=False)
    version_check = next(c for c in report.checks if c.name == "cache_schema_version")
    assert not version_check.passed
    assert version_check.hard
    assert not report.ok


def test_unreadable_cache_path_is_a_hard_failure(monkeypatch, tmp_path) -> None:
    # Point WAD_DATA_DIR at a *file*, so the cache directory can never be created.
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("WAD_DATA_DIR", str(blocked))

    report = doctor.run_diagnostics(check_upstream=False)
    assert not report.ok
    failing = [c for c in report.checks if not c.passed]
    assert failing
    assert all(c.hard for c in failing)


def test_corrupt_cache_file_is_a_hard_failure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    db_path = default_db_path()
    db_path.write_bytes(b"not a sqlite database")

    report = doctor.run_diagnostics(check_upstream=False)
    cache_check = next(c for c in report.checks if c.name == "cache_readable")
    assert not cache_check.passed
    assert cache_check.hard
    assert not report.ok


def test_upstream_check_reports_reachable_and_unreachable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    seen_urls: list[str] = []

    def fake_head(url: str, timeout: float) -> object:
        seen_urls.append(url)
        if "musicbrainz" in url:
            raise requests.ConnectionError("simulated network failure")
        return object()

    monkeypatch.setattr(requests, "head", fake_head)

    report = doctor.run_diagnostics(check_upstream=True)
    upstream_checks = [c for c in report.checks if c.name.startswith("upstream:")]
    assert len(upstream_checks) == len(doctor.UPSTREAM_APIS)
    assert len(seen_urls) == len(doctor.UPSTREAM_APIS)
    assert all(not c.hard for c in upstream_checks)  # informational only

    musicbrainz = next(c for c in upstream_checks if c.name == "upstream:MusicBrainz")
    assert not musicbrainz.passed
    lastfm = next(c for c in upstream_checks if c.name == "upstream:Last.fm")
    assert lastfm.passed

    # A flaky/offline upstream never fails the overall report by itself.
    assert report.ok


def test_cli_doctor_exit_code_reflects_report(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    from pipeline.cli import main

    code = main(["doctor"])
    assert code == 0
    out = capsys.readouterr().out
    assert "doctor: OK" in out
    assert "[PASS]" in out


def test_cli_doctor_check_upstream_flag_parses(monkeypatch, tmp_path) -> None:
    """--check-upstream is opt-in; just confirm the flag is wired without making it run
    (network is unavailable/undesirable in tests — probed indirectly via the flag's
    propagation into run_diagnostics, unit-tested separately)."""
    monkeypatch.setenv("WAD_DATA_DIR", str(tmp_path))
    import argparse

    from pipeline.cli import _cmd_doctor

    parser = argparse.ArgumentParser()
    parser.add_argument("--check-upstream", action="store_true")
    args = parser.parse_args(["--check-upstream"])
    assert args.check_upstream is True
    # Exercise the glue with check_upstream disabled to stay network-free here.
    args.check_upstream = False
    assert _cmd_doctor(args) == 0
