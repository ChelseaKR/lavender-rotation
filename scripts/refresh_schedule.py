#!/usr/bin/env python3
"""Print the scheduler entry that runs `make refresh` on the recorded cadence.

`lavender refresh --user` closes the upstream-correction round-trip: it re-asks
MusicBrainz and Wikidata about cached artists and reconciles the pending ledger
against what actually came back. Until now nothing ran it on a schedule, so an
artist who corrected their own record upstream reached this project only when
somebody remembered to re-ask. That is a correctness gap, not a convenience one.

**Why this is not a GitHub Actions cron.** The thing being refreshed is a cache
of *your* listening history, in the platform user-data directory on *your*
machine (`pipeline/paths.py`). A hosted runner has no such cache; giving it one
would mean uploading a personal listening profile to CI, which contradicts the
local-first promise in `docs/audits/privacy-notes.md` and buys nothing. A
workflow that cannot reach the data it claims to refresh is worse than no
scheduler: it is a green checkmark for work that did not happen. ADR 0013
records the decision.

So the scheduler is the operator's own — launchd on macOS, cron elsewhere — and
this script renders the entry for *this* checkout so no path has to be typed by
hand. It writes nothing and installs nothing; installing a job that runs on your
behalf is a deliberate, human copy-paste.

**Credentials are never rendered into the entry.** A launchd agent under
`~/Library/LaunchAgents` is a plain file, and a crontab is readable by root and
by anything that backs up your home directory; an API key does not belong in
either. Both entries instead source a mode-600 env file that the operator
creates once. `tests/test_refresh_schedule.py` asserts no rendered entry ever
carries a credential value.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import sys
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The cadence decision (ADR 0013), in one place. Both renderers express it, and
#: `scripts/docs_figures.py` holds the prose that states it to this number.
CADENCE_DAYS = 7

#: Weekly, on a Tuesday, at 04:17 local time. Off the hour and off the top of the
#: week on purpose: the neighboring `.github/workflows/*.yml` crons are staggered
#: for the same reason, and MusicBrainz is a volunteer-run service whose rate
#: limit this project honors rather than tests.
CRON_WEEKDAY = 2
RUN_HOUR = 4
RUN_MINUTE = 17

#: launchd's own numbering: 0 and 7 are both Sunday, so the two agree here.
LAUNCHD_WEEKDAY = CRON_WEEKDAY

#: A reverse-DNS label, as launchd expects, and the agent filename.
LABEL = "com.chelseakr.lavender-rotation.refresh"

#: Where the two live-mode variables live. Created by the operator, mode 600,
#: never written by this script:
#:
#:     export LAVENDER_LASTFM_API_KEY=...
#:     export LAVENDER_CONTACT=you@example.org
ENV_FILE = "$HOME/.config/lavender-rotation/env"

Scheduler = Literal["launchd", "cron"]


def default_scheduler(platform: str | None = None) -> Scheduler:
    """launchd on macOS, cron everywhere else."""
    return "launchd" if (platform or sys.platform) == "darwin" else "cron"


def log_path(platform: str | None = None, *, home: Path | None = None) -> str:
    """Where a scheduled run's output goes — absolute, never `$HOME`.

    launchd performs no shell expansion on `StandardOutPath`, so a `$HOME` in
    that key is a literal directory name and the log silently goes nowhere the
    operator will look. The path is resolved here instead, on the machine the
    entry is being rendered for.

    A refresh that finds the upstream unreachable exits non-zero and says so
    (`RefreshOutcome`); a scheduled run nobody can read would turn that honest
    failure into silence, which is the exact shape of bug the refresh path was
    written to avoid.
    """
    root = home or Path.home()
    if (platform or sys.platform) == "darwin":
        return str(root / "Library" / "Logs" / "lavender-rotation-refresh.log")
    return str(root / ".local" / "state" / "lavender-rotation" / "refresh.log")


def refresh_command(user: str, *, repo: Path = REPO_ROOT) -> str:
    """The one command both schedulers run.

    `make refresh` rather than the CLI directly: the Makefile is this repo's
    single source of truth for how anything is invoked, so the scheduled run and
    the hand-run stay the same run. `-C` because a scheduler has no working
    directory of yours.
    """
    return f'. "{ENV_FILE}" && make -C "{repo}" refresh LAVENDER_USER={user}'


def render_launchd(user: str, *, repo: Path = REPO_ROOT, platform: str | None = None) -> str:
    """A launchd user agent, for `~/Library/LaunchAgents/<LABEL>.plist`.

    Built through :mod:`plistlib` rather than an f-string of XML. The command it
    carries contains `&&`, which is not a legal raw character in XML text — a
    hand-written template produced a plist that launchd would have rejected, and
    the escaping rules are not worth re-implementing to find that out again.

    `StartCalendarInterval` rather than `StartInterval`: a calendar entry runs at
    a stated local time, while an interval drifts with every reboot and fires a
    catch-up run on load. A re-check that quietly moves is a cadence nobody can
    state.
    """
    if CADENCE_DAYS != 7:
        raise ValueError(
            f"CADENCE_DAYS is {CADENCE_DAYS}; this renderer only knows how to express a "
            "weekly calendar entry. Change the renderer and ADR 0013 together, deliberately."
        )
    log = log_path(platform)
    agent = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", "-c", refresh_command(user, repo=repo)],
        "StartCalendarInterval": {
            "Weekday": LAUNCHD_WEEKDAY,
            "Hour": RUN_HOUR,
            "Minute": RUN_MINUTE,
        },
        "StandardOutPath": log,
        "StandardErrorPath": log,
        # A missed weekly run must not fire the moment the agent is loaded: a
        # bounded refresh at login is a surprise upstream fetch nobody asked for.
        "RunAtLoad": False,
    }
    return plistlib.dumps(agent, fmt=plistlib.FMT_XML).decode("utf-8")


def render_cron(user: str, *, repo: Path = REPO_ROOT, platform: str | None = None) -> str:
    """One crontab line, for `crontab -e`."""
    if CADENCE_DAYS != 7:
        raise ValueError(
            f"CADENCE_DAYS is {CADENCE_DAYS}; this renderer only knows how to express a "
            "weekly crontab field. Change the renderer and ADR 0013 together, deliberately."
        )
    command = refresh_command(user, repo=repo)
    log = log_path(platform)
    # `mkdir -p` because cron will not create the log's directory and a redirect
    # into a missing one fails before the refresh ever starts — silently, since
    # the place the error would have been written is the thing that is missing.
    directory = str(Path(log).parent)
    return (
        f'{RUN_MINUTE} {RUN_HOUR} * * {CRON_WEEKDAY} mkdir -p "{directory}" && '
        f'{command} >> "{log}" 2>&1\n'
    )


INSTALL_NOTES = f"""\
# Before this runs, create the env file it sources (once, mode 600 — a scheduler
# entry must never carry a credential):
#
#   mkdir -p ~/.config/lavender-rotation
#   printf 'export LAVENDER_LASTFM_API_KEY=...\\nexport LAVENDER_CONTACT=you@example.org\\n' \\
#     > ~/.config/lavender-rotation/env
#   chmod 600 ~/.config/lavender-rotation/env
#
# Cadence: every {CADENCE_DAYS} days (ADR 0013). One run is bounded by
# `lavender refresh --limit`; runs rotate through the catalog stalest-first, so a
# whole catalog is several runs and each one resumes where the last stopped.
# A run that reaches nothing exits non-zero and says the upstream was unreachable
# — read the log rather than assuming silence means agreement.
"""

_LAUNCHD_INSTALL = f"""\
# Install (macOS):
#   make schedule LAVENDER_USER=<you> > ~/Library/LaunchAgents/{LABEL}.plist
#   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{LABEL}.plist
# Remove:
#   launchctl bootout gui/$(id -u)/{LABEL}
"""

_CRON_INSTALL = """\
# Install (cron): run `crontab -e` and paste the line below.
# Remove: run `crontab -e` and delete it.
"""


def render(user: str, scheduler: Scheduler, *, repo: Path = REPO_ROOT) -> str:
    """The full printable entry: install notes, then the artifact itself."""
    if scheduler == "launchd":
        return INSTALL_NOTES + _LAUNCHD_INSTALL + "\n" + render_launchd(user, repo=repo)
    return INSTALL_NOTES + _CRON_INSTALL + "\n" + render_cron(user, repo=repo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--user", required=True, help="the Last.fm username whose catalog to refresh"
    )
    parser.add_argument(
        "--scheduler",
        choices=("launchd", "cron"),
        default=None,
        help="which scheduler to render for (default: launchd on macOS, cron elsewhere)",
    )
    args = parser.parse_args(argv)

    scheduler: Scheduler = args.scheduler or default_scheduler()
    if os.environ.get("LAVENDER_LASTFM_API_KEY"):
        print(
            "note: an API key is set in this shell and is deliberately NOT rendered "
            f"below — put it in {ENV_FILE} (mode 600) instead.",
            file=sys.stderr,
        )
    print(render(args.user, scheduler), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
