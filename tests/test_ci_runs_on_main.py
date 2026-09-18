"""`ci.yml` must run on a push to `main`, and merge runs must not evict one another.

Both properties have already been lost once here, quietly, and neither shows up
as a red build when it goes:

1. **The `push` trigger.** It was removed on 2026-07-16 (#43) for a reason that
   was true at the time — the workflow then ran a four-runtime matrix on
   non-pull-request events, so a merge run really was four more jobs than the
   pull-request gate. #85 deleted that ternary on 2026-08-15 and the reason
   stopped being true, but nothing re-derived it, so the trigger stayed off.
   Measured on 2026-09-10 over the last 40 commits on `main`: **8 carry a `ci`
   run on that exact SHA**, and all 8 are `schedule` or `workflow_dispatch`
   runs. Not one merge commit has ever been verified by this workflow, and the
   sentence "main is green" was therefore a statement about somebody's branch.

2. **The concurrency key.** With `group: ci-${{ github.ref }}` and
   `cancel-in-progress: true`, every merge run shares one group, so two merges
   minutes apart leave the first commit with a *cancelled* run. A cancelled run
   reads as a failure, proves nothing about the tree, and is indistinguishable
   from a job killed by its own timeout without reading the job's `steps`
   array. Restoring the trigger under that key would have bought a verdict on
   `main` that silently is not one.

These are read out of the file as text rather than with a YAML parser, which is
the same choice `test_trufflehog_workflow.py` makes and for a sharper reason
here: PyYAML implements YAML 1.1, in which the bare key `on` is the **boolean
`True`**, so `yaml.safe_load(...)["on"]` returns `None` over a workflow that
plainly declares triggers. Reading the block by indentation cannot make that
mistake, and it also sees `push:` with an empty body — a declared trigger that
fires on every branch and every tag — which a `is None` test would call absent.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _top_level_block(source: str, key: str) -> list[str]:
    """The lines indented under a top-level ``key:``, excluding comments.

    Returns ``[]`` when the key is not declared at the top level at all, which
    is a different fact from a key whose block is empty — the caller
    distinguishes them, because a trigger declared with no body still fires.
    """
    lines = source.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            start = index
            break
    if start is None:
        return []
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        if line.strip().startswith("#") or not line.strip():
            continue
        block.append(line)
    return block


def _declared_triggers() -> list[str]:
    """The event names `on:` declares, in file order."""
    block = _top_level_block(WORKFLOW.read_text(encoding="utf-8"), "on")
    return [
        line.strip().rstrip(":")
        for line in block
        if line.startswith("  ") and not line.startswith("   ") and line.strip().endswith(":")
    ]


def test_the_trigger_reader_finds_the_events_this_workflow_has_always_had() -> None:
    """A floor under the reader itself.

    Every assertion below is of the form "this event is declared". A reader
    that stopped matching returns an empty list, and an empty list satisfies
    nothing here but would satisfy a differently-worded test. Pinning two
    events that have been in this file since it was written means a broken
    reader fails loudly instead of reporting the file clean.
    """
    triggers = _declared_triggers()
    assert triggers, "read no triggers at all out of ci.yml: the block reader is broken"
    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers


def test_ci_runs_on_a_push_to_main() -> None:
    """Without this, no commit that reaches `main` is ever verified as merged."""
    triggers = _declared_triggers()
    assert "push" in triggers, (
        "ci.yml declares no push trigger, so no run is ever recorded against a "
        "commit on main. Measured 2026-09-10: 8 of the last 40 main commits "
        "carried a ci run, none of them a merge."
    )


def test_the_push_trigger_names_main() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    block = _top_level_block(source, "on")
    body: list[str] = []
    collecting = False
    for line in block:
        if line.strip() == "push:":
            collecting = True
            continue
        if collecting and line.startswith("  ") and not line.startswith("    "):
            break
        if collecting:
            body.append(line.strip())
    assert body, (
        "`push:` is declared with an empty body, which fires on every branch "
        "and every tag rather than on main"
    )
    assert any("main" in entry for entry in body), f"push trigger does not name main: {body}"


def test_a_merge_run_cannot_evict_another_merge_run() -> None:
    """The concurrency group must key on the commit outside pull requests.

    `github.ref` is the same string for every push to `main`, so under it the
    second of two quick merges cancels the first and leaves that commit with a
    cancelled run — a verdict that is not one.
    """
    source = WORKFLOW.read_text(encoding="utf-8")
    block = _top_level_block(source, "concurrency")
    group = next((line for line in block if line.strip().startswith("group:")), "")
    cancel = next((line for line in block if line.strip().startswith("cancel-in-progress:")), "")
    assert group, "ci.yml declares no concurrency group"
    assert "github.sha" in group, (
        "the concurrency group does not key on the commit outside pull "
        f"requests, so two merges share a slot: {group.strip()}"
    )
    assert "github.ref" not in group, (
        "github.ref is one value for every push to main, so keying on it "
        f"lets one merge run evict another: {group.strip()}"
    )
    assert "github.event_name == 'pull_request'" in cancel, (
        "cancel-in-progress must be limited to pull requests; cancelling a "
        f"merge run destroys the only verdict that commit will get: {cancel.strip()}"
    )
