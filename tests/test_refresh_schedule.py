"""The rendered scheduler entry must run the documented command, and carry no secret.

`scripts/refresh_schedule.py` prints a launchd agent or a crontab line that the
operator pastes into a place where it will run unattended, on their machine,
with their credentials in scope. Three things about that are worth gating:

* **It must run what the docs say it runs.** A scheduler entry that drifts from
  `make refresh` is a cadence nobody can verify — the whole point of ADR 0013 is
  that the scheduled run and the hand-run are the same run.
* **It must never carry a credential.** A launchd agent under
  `~/Library/LaunchAgents` is a plain file; a crontab is readable by root and by
  anything that backs up a home directory. The entry sources a mode-600 env file
  instead, and this asserts that a key present in the rendering environment does
  not end up in the rendering.
* **Its paths must be absolute.** launchd performs no shell expansion on
  `StandardOutPath`, so a `$HOME` there is a literal directory name and the log
  goes somewhere nobody will look — the silent-failure shape this repo keeps
  finding and fixing.

Nothing here installs anything or opens a socket; the renderers are pure string
functions over a repo path and a username.
"""

from __future__ import annotations

import importlib.util
import plistlib
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE_REPO = Path("/opt/checkouts/lavender-rotation")
FAKE_HOME = Path("/Users/fake")


def _load_refresh_schedule() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "refresh_schedule", REPO_ROOT / "scripts" / "refresh_schedule.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["refresh_schedule"] = module
    spec.loader.exec_module(module)
    return module


rs = _load_refresh_schedule()


# --- the command both schedulers run ---------------------------------------


def test_the_command_invokes_the_make_target_and_not_the_cli_directly() -> None:
    """`make refresh` is the seam. Calling `pipeline.cli` here would be a second
    place for the invocation to drift from the one the Makefile documents."""
    command = rs.refresh_command("someone", repo=FAKE_REPO)
    assert f'make -C "{FAKE_REPO}" refresh LAVENDER_USER=someone' in command


def test_the_command_sources_the_env_file_before_running() -> None:
    command = rs.refresh_command("someone", repo=FAKE_REPO)
    assert command.startswith(f'. "{rs.ENV_FILE}" &&'), command


def test_the_make_target_the_command_names_exists() -> None:
    """The renderer's `make refresh` must be a real target, not a hopeful string."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^refresh:", makefile, re.MULTILINE), "Makefile has no `refresh` target"
    assert re.search(r"^\.PHONY:.*\brefresh\b", makefile, re.MULTILINE)


def test_the_make_target_passes_the_username_variable_the_renderer_sets() -> None:
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\nrefresh:", 1)[1].split("\n\n", 1)[0]
    assert "$(LAVENDER_USER)" in recipe, recipe
    assert "refresh --user" in recipe, recipe


def test_both_schedulers_run_the_same_command() -> None:
    """Read the plist back through plistlib rather than substring-matching the
    XML: the command contains `&&`, which is escaped in the document and is only
    the same string once parsed. Comparing the raw text would have this test
    passing on an entry launchd could not read."""
    command = rs.refresh_command("someone", repo=FAKE_REPO)
    agent = plistlib.loads(
        rs.render_launchd("someone", repo=FAKE_REPO, platform="darwin").encode("utf-8")
    )
    assert agent["ProgramArguments"][2] == command
    assert command in rs.render_cron("someone", repo=FAKE_REPO, platform="linux")


# --- no credential in the entry --------------------------------------------

_SECRETS = ("LAVENDER_LASTFM_API_KEY", "LAVENDER_CONTACT")


@pytest.mark.parametrize("variable", _SECRETS)
def test_no_rendered_entry_carries_a_credential_value(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    """The value must stay in the env file even when it is sitting in the
    environment of the shell doing the rendering."""
    sentinel = "s3cr3t-should-never-be-rendered"
    monkeypatch.setenv(variable, sentinel)
    entries = (
        rs.render_launchd("someone", repo=FAKE_REPO, platform="darwin"),
        rs.render_cron("someone", repo=FAKE_REPO, platform="linux"),
    )
    for rendered in (*entries, rs.render("someone", "launchd", repo=FAKE_REPO)):
        assert sentinel not in rendered
    for entry in entries:
        # The *entry* must not assign the variable at all — only the human
        # install notes may mention it, and only to say where its value belongs.
        assert f"{variable}=" not in entry


def test_the_entry_points_at_the_env_file_that_holds_them_instead() -> None:
    for rendered in (
        rs.render("someone", "launchd", repo=FAKE_REPO),
        rs.render("someone", "cron", repo=FAKE_REPO),
    ):
        assert rs.ENV_FILE in rendered
        assert "chmod 600" in rendered


# --- launchd ---------------------------------------------------------------


def test_the_launchd_agent_is_a_parseable_plist() -> None:
    parsed = plistlib.loads(
        rs.render_launchd("someone", repo=FAKE_REPO, platform="darwin").encode("utf-8")
    )
    assert parsed["Label"] == rs.LABEL
    assert parsed["ProgramArguments"][:2] == ["/bin/sh", "-c"]
    assert parsed["RunAtLoad"] is False


def test_the_launchd_agent_states_a_calendar_time_not_an_interval() -> None:
    """An interval drifts with every reboot and fires a catch-up run on load; a
    calendar entry runs at the stated local time, which is what ADR 0013 records."""
    parsed = plistlib.loads(
        rs.render_launchd("someone", repo=FAKE_REPO, platform="darwin").encode("utf-8")
    )
    assert "StartInterval" not in parsed
    interval = parsed["StartCalendarInterval"]
    assert interval == {
        "Weekday": rs.LAUNCHD_WEEKDAY,
        "Hour": rs.RUN_HOUR,
        "Minute": rs.RUN_MINUTE,
    }


def test_the_launchd_log_paths_are_absolute_because_launchd_expands_nothing() -> None:
    parsed = plistlib.loads(
        rs.render_launchd("someone", repo=FAKE_REPO, platform="darwin").encode("utf-8")
    )
    for key in ("StandardOutPath", "StandardErrorPath"):
        assert parsed[key] == parsed["StandardOutPath"]
        assert Path(parsed[key]).is_absolute()
        assert "$HOME" not in parsed[key] and "~" not in parsed[key]


# --- cron ------------------------------------------------------------------


def test_the_cron_line_is_one_line_with_the_recorded_time_fields() -> None:
    line = rs.render_cron("someone", repo=FAKE_REPO, platform="linux")
    assert line.count("\n") == 1
    minute, hour, dom, month, weekday = line.split()[:5]
    assert (int(minute), int(hour)) == (rs.RUN_MINUTE, rs.RUN_HOUR)
    assert (dom, month) == ("*", "*")
    assert int(weekday) == rs.CRON_WEEKDAY


def test_the_cron_line_creates_the_log_directory_before_redirecting_into_it() -> None:
    """cron will not create it, and a redirect into a missing directory fails
    before the refresh starts — with the error going to the place that is missing."""
    line = rs.render_cron("someone", repo=FAKE_REPO, platform="linux")
    log = rs.log_path("linux")
    assert f'mkdir -p "{Path(log).parent}"' in line
    assert line.rstrip().endswith(f'>> "{log}" 2>&1')


# --- the cadence -----------------------------------------------------------


def test_the_cadence_is_weekly_and_the_two_renderers_agree_on_the_day() -> None:
    assert rs.CADENCE_DAYS == 7
    assert rs.LAUNCHD_WEEKDAY == rs.CRON_WEEKDAY


def test_a_cadence_the_renderers_cannot_express_is_an_error_not_a_wrong_entry() -> None:
    """Both renderers write a weekly field. Changing the constant without
    changing them would silently keep emitting a weekly schedule under a
    different documented cadence."""
    original = rs.CADENCE_DAYS
    try:
        rs.CADENCE_DAYS = 3
        for render in (rs.render_launchd, rs.render_cron):
            with pytest.raises(ValueError, match="weekly"):
                render("someone", repo=FAKE_REPO, platform="darwin")
    finally:
        rs.CADENCE_DAYS = original


def test_the_adr_that_records_the_cadence_exists_and_is_accepted() -> None:
    adr = REPO_ROOT / "docs" / "adr" / "0013-local-refresh-schedule-not-hosted-cron.md"
    text = adr.read_text(encoding="utf-8")
    assert "## Status\n\nAccepted" in text
    assert f"CADENCE_DAYS = {rs.CADENCE_DAYS}" in text


def test_no_workflow_schedules_the_live_refresh() -> None:
    """ADR 0013's central claim, held to the workflows themselves: a hosted runner
    has no cache to refresh, so a cron that appeared to do it would be reporting
    green for work that did not happen."""
    for workflow in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        assert "cli refresh" not in text and "lavender refresh" not in text, workflow.name
        assert "make refresh" not in text, workflow.name


# --- platform defaults -----------------------------------------------------


def test_the_default_scheduler_follows_the_platform() -> None:
    assert rs.default_scheduler("darwin") == "launchd"
    assert rs.default_scheduler("linux") == "cron"


def test_the_log_path_follows_the_platform_and_is_absolute() -> None:
    mac = rs.log_path("darwin", home=FAKE_HOME)
    linux = rs.log_path("linux", home=FAKE_HOME)
    assert mac == str(FAKE_HOME / "Library" / "Logs" / "lavender-rotation-refresh.log")
    assert linux == str(FAKE_HOME / ".local" / "state" / "lavender-rotation" / "refresh.log")
    assert Path(mac).is_absolute() and Path(linux).is_absolute()
