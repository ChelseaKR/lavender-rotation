"""`ci.yml`'s secret scan must read history, not the range the event handed it.

The merge gate here is `verify (3.12)` / `verify (3.13)`. Its secret-scan layer was
`gitleaks/gitleaks-action`, which chooses its scan range from the triggering event:

    pull_request  the pull request's own commits
    push (1)      gitleaks detect --log-opts=-1
    schedule / workflow_dispatch    no --log-opts, i.e. the whole history

`ci.yml` runs on `pull_request`, `schedule` and `workflow_dispatch`, so the lane that
gates a merge read a pull request's own commits and nothing older, while the Tuesday
cron read all of them. A credential added and removed before the branch point was
invisible to the gate.

`fetch-depth: 0` was on that checkout throughout and did not prevent it. It decides
how much history `actions/checkout` puts on disk, never how much of it the scanner is
asked to read; the comment on that line claimed the opposite and is corrected.

`make security`'s `scripts/secret-scan.sh` is not the missing coverage either: it
`exec`s `gitleaks detect` only when the binary is on PATH, and nothing installs it
before `make verify` runs, so in CI it takes its grep fallback over the working tree.

Measured on a throwaway clone with its remote removed: a random real-shaped AWS key
planted in one commit and deleted in the next left `gitleaks git . --log-opts=-1`
exiting 0 and `gitleaks git .` exiting 1.
"""

from __future__ import annotations

import re
from pathlib import Path

# From this file, not from an installed package's notion of the repository root: in a
# git worktree whose venv points at the primary checkout, a package-derived root names
# the OTHER tree, and these assertions would then read a file this branch never edited.
CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"

# The comment above the step names both the action that was removed and the flag that
# must not return. Assertions that read comments are how four conformance checks in
# this portfolio passed on prose, so every assertion below reads the file stripped.
_COMMENT = re.compile(r"(?m)^\s*#.*$|\s+#.*$")


def _ci_code() -> str:
    return _COMMENT.sub("", CI.read_text(encoding="utf-8"))


def test_the_comment_stripper_actually_strips() -> None:
    """A no-op stripper would make every assertion below vacuous."""
    raw = CI.read_text(encoding="utf-8")
    assert "gitleaks/gitleaks-action" in raw, (
        "the explanatory comment naming the removed action is gone, so this test can no "
        "longer prove the stripper works; point it at another comment-only string"
    )
    assert "gitleaks/gitleaks-action" not in _ci_code()


def test_the_scanner_is_not_handed_a_range() -> None:
    code = _ci_code()
    assert "gitleaks git . --no-banner --redact --exit-code 1" in code, (
        "the secret scan no longer runs `gitleaks git .`. Whatever replaces it must "
        "still walk the history on every event, not the event's own range."
    )
    assert "--log-opts" not in code, (
        "`--log-opts` scopes gitleaks to a commit range. A range chosen from the "
        "triggering event is what made this gate blind to anything before the branch."
    )


def test_the_event_driven_action_does_not_come_back() -> None:
    assert "gitleaks/gitleaks-action" not in _ci_code(), (
        "gitleaks/gitleaks-action takes its range from the event: a pull request's own "
        "commits on `pull_request`, `--log-opts=-1` on a one-commit push."
    )


def test_checkout_still_fetches_the_history_the_scan_walks() -> None:
    """Necessary, not sufficient: without it there is nothing on disk to walk."""
    assert re.search(r"^\s*fetch-depth:\s*0\s*$", _ci_code(), flags=re.MULTILINE), (
        "`fetch-depth: 0` is gone from the verify checkout, so `gitleaks git .` would "
        "walk the single commit actions/checkout fetched. This is the precondition for "
        "a history scan; the invocation above is what makes it one."
    )


def test_the_pinned_binary_is_checksum_verified() -> None:
    code = _ci_code()
    assert "gitleaks_checksums.txt" in code and "sha256sum --check --strict" in code, (
        "the gitleaks binary is downloaded without verifying its published checksum"
    )
