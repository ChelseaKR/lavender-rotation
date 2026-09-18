"""The committed ruleset must not be a lockout waiting to be applied.

`docs/audits/branch-ruleset.json` is a declarative GitHub ruleset, and ADR 0001
described it as a target to be posted by hand with `gh api -X POST`. It carried
`"bypass_actors": []` from the day it was written, and ADR 0001 argued for that
emptiness on purpose: "not even the repository owner/admin can merge around the
ruleset ... an empty `bypass_actors` list is sufficient". That reasoning is
wrong. GitHub accepts such an apply with a 201, and the result is a `main` that
the owner cannot merge to, push to, or unblock, because unblocking it means
editing the ruleset that is doing the blocking. It is not hypothetical: applying
a no-bypass ruleset elsewhere in this portfolio took a recovery sweep across
eighteen repositories.

Correcting the file once is not the fix, because the file can regress. This
module is the fix: the empty list, and the four neighboring ways to lose the
bypass, are now test failures.

The checks fail closed, in the same spirit as `tests/test_no_inference.py`:
`lockout_risk` is a pure function of a parsed document so it can be run against
documents it must reject as well as against the committed one, and
`load_ruleset` treats a missing or unparseable file as a failure rather than
returning an empty document the assertions below would read as "nothing wrong".
A guard that passes when its subject is absent is the defect it exists to catch,
and the parse is what catches it: a truncated file still contains the literal
string `bypass_actors`, so a grep would wave it through.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RULESET = REPO_ROOT / "docs" / "audits" / "branch-ruleset.json"
RULESET_ADR = REPO_ROOT / "docs" / "adr" / "0001-single-maintainer-review-posture.md"

OWNER_BYPASS: dict[str, Any] = {
    "actor_id": 5,
    "actor_type": "RepositoryRole",
    "bypass_mode": "always",
}
"""The owner's standing bypass, and the only entry this file may carry.

`RepositoryRole` 5 is admin. `bypass_mode: "always"` rather than the
`pull_request` an internal CI/CD standard suggests, because a bypass that only
works inside a pull request is no use when the pull request is the thing that is
wedged.
"""


def load_ruleset() -> dict[str, Any]:
    """The committed ruleset, or a failure. Never a silent empty document.

    The two ways a check like this passes vacuously are a missing file and an
    unparseable one, so both are failures here rather than defaults.
    """
    if not RULESET.is_file():
        pytest.fail(f"{RULESET} is missing; the committed ruleset is what this checks")
    try:
        loaded = json.loads(RULESET.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"{RULESET} is not parseable JSON, so nothing can vouch for it: {exc}")
    if not isinstance(loaded, dict):
        pytest.fail(f"{RULESET} is not a JSON object")
    return loaded


def lockout_risk(ruleset: dict[str, Any]) -> str | None:
    """Why applying this document would lock the owner out, or ``None`` if it would not.

    A pure function of a parsed document, so it can be exercised against the
    documents it must reject and not only against the one that is committed.
    """
    if "bypass_actors" not in ruleset:
        return "no bypass_actors key at all, which GitHub reads as an empty list"
    actors = ruleset["bypass_actors"]
    if not isinstance(actors, list):
        return f"bypass_actors is {type(actors).__name__}, not a list"
    if not actors:
        return (
            "bypass_actors is empty, so applying this leaves no break-glass path and the "
            "owner cannot merge, push, or delete the ruleset that is blocking them"
        )
    if OWNER_BYPASS not in actors:
        return (
            f"bypass_actors does not carry the owner's standing bypass {OWNER_BYPASS}; "
            f"it carries {actors}"
        )
    return None


def test_applying_the_committed_ruleset_would_not_lock_the_owner_out() -> None:
    """The whole point. This is the assertion the empty list has to fail."""
    risk = lockout_risk(load_ruleset())
    assert risk is None, (
        "applying docs/audits/branch-ruleset.json as committed would lock the repository "
        f"owner out: {risk}"
    )


def test_the_owner_role_bypass_is_the_only_entry_the_file_carries() -> None:
    """One actor. A second entry is a widening of who can skip every rule.

    The live ruleset carries a second actor this file deliberately does not
    (see ADR 0001's 2026-08-29 correction). Recording it here would re-assert an
    unreviewed widening on the next apply, so the file states the reviewed
    posture and the ADR states the disagreement.
    """
    actors = load_ruleset()["bypass_actors"]
    assert actors == [OWNER_BYPASS], (
        "the owner's role bypass is the only entry this file may carry, and a second one "
        f"would be applied to the server verbatim: {actors}"
    )


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"bypass_actors": []}, "empty"),
        ({}, "no bypass_actors key"),
        ({"bypass_actors": {}}, "not a list"),
        (
            {
                "bypass_actors": [
                    {"actor_id": 1, "actor_type": "Integration", "bypass_mode": "always"}
                ]
            },
            "does not carry the owner",
        ),
        (
            {"bypass_actors": [dict(OWNER_BYPASS, bypass_mode="pull_request")]},
            "does not carry the owner",
        ),
    ],
    ids=["empty", "absent", "wrong-type", "wrong-actor", "wrong-mode"],
)
def test_the_lockout_check_rejects_the_documents_it_must_reject(
    document: dict[str, Any], expected: str
) -> None:
    """Five ways to lose the bypass, each of which GitHub answers with a 201 like any other apply.

    The empty list is the one that was committed. `wrong-mode` is the subtle
    one: `bypass_mode: "pull_request"` looks like a bypass and satisfies the
    CI/CD standard that asks for it, but it does not help when the pull request
    is what is stuck. The rest are shapes an edit to fix the empty list could
    plausibly land in.
    """
    risk = lockout_risk(document)
    assert risk is not None, f"{document} should be refused"
    assert expected in risk


def test_the_lockout_check_accepts_the_shape_it_should() -> None:
    """A positive control, so the rejections above cannot be passing by refusing everything."""
    assert lockout_risk({"bypass_actors": [OWNER_BYPASS]}) is None


def test_the_adr_names_the_bypass_the_file_carries() -> None:
    """ADR 0001 is the prose a person reads before posting this file, and prose drifts.

    If the record and the file disagree about the bypass, the record is the one
    someone follows.
    """
    adr = RULESET_ADR.read_text(encoding="utf-8")
    for fragment in ('"actor_id": 5', "RepositoryRole", "always"):
        assert fragment in adr, f"{RULESET_ADR} does not name {fragment!r}"
