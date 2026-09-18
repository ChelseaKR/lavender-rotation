"""The scheduled secret scan must stay capable of failing.

Three properties of `.github/workflows/trufflehog.yml` are asserted here, because
each one has silently un-armed a secret scan in this portfolio and none of them
shows up as a red build when it breaks:

1. **The result tiers include `unverified`.** TruffleHog sorts a finding into
   `verified` (it authenticated the credential against the live service),
   `unknown` (verification errored) and `unverified` (it asked, and the service
   said no). A credential that leaked and was later *revoked* — the normal end
   state of a real leak, and the exact case a scheduled full-history sweep
   exists to catch — answers "no" and is therefore `unverified`. A scan
   configured `--only-verified`, or `--results=verified,unknown`, cannot fail on
   it. Measured 2026-09-06 on a throwaway clone of this repository with a
   real-shaped AWS key planted in one commit and deleted in the next:
   `--results=verified` exited 0 reporting nothing; the widened tier exited 183.

2. **The action ref and the `version:` input name the same release.** The
   `version:` input is what selects the scanning binary
   (`ghcr.io/trufflesecurity/trufflehog:${VERSION}`); the `uses:` SHA pins only
   the wrapper. Dependabot edits `uses:` and never a `with:` input, so the two
   drift apart and each "upgrade" is a no-op that reads like one. This
   repository was in exactly that state: the ref said v3.95.8 while the scanner
   downloaded 3.96.0.

3. **`fetch-depth: 0` survives on the checkout.** A *precondition*, not the
   cause. Without it `actions/checkout` fetches a single commit and there is
   nothing on disk to walk; with it, and with a range-scoped invocation, the
   whole history sits in the checkout while the scanner reads two commits. That
   is not hypothetical here: `ci.yml`'s gitleaks step carried `fetch-depth: 0`
   for months while `gitleaks/gitleaks-action` scanned only the pull request's
   own commits. What a scanner reads is decided by how it is INVOKED. This
   workflow runs only on `schedule` and `workflow_dispatch`, the two events for
   which the TruffleHog action resolves its range to `--since-commit ""
   --branch ""`, so here the invocation is already a whole-history one and this
   assertion guards the precondition it needs.

The workflow's pin comment is a YAML comment, so it is invisible to a YAML
parser: these assertions read the file as text on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "trufflehog.yml"

# The tier that a revoked credential lands in. Its absence is the defect.
REQUIRED_RESULT_TIER = "unverified"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_exists() -> None:
    assert WORKFLOW.is_file(), f"{WORKFLOW} is missing; the scheduled secret scan is gone"


def test_scan_reports_the_unverified_tier() -> None:
    """A revoked credential is `unverified`; excluding that tier disarms the scan."""
    text = _workflow_text()

    extra_args = re.findall(r"^\s*extra_args:\s*(.+?)\s*$", text, flags=re.MULTILINE)
    assert extra_args, "no `extra_args:` found; this guard can no longer see the tiers"

    for args in extra_args:
        assert "--only-verified" not in args, (
            "`--only-verified` cannot fail on a revoked credential, which is the "
            "normal end state of a real leak and the case this scan exists for. "
            f"Offending args: {args!r}"
        )
        results = re.search(r"--results=([\w,]+)", args)
        assert results, f"expected an explicit `--results=` tier list, got {args!r}"
        tiers = results.group(1).split(",")
        assert REQUIRED_RESULT_TIER in tiers, (
            f"`--results={results.group(1)}` omits `{REQUIRED_RESULT_TIER}`, so the scan "
            "cannot fail on a credential the provider has already revoked. "
            "Measured: verified / verified,unknown both exit 0 on a planted-then-deleted "
            "AWS key; adding unverified exits 183."
        )


def test_action_ref_and_version_input_name_the_same_release() -> None:
    """`version:` selects the binary; `uses:` pins only the wrapper. They must agree."""
    text = _workflow_text()

    pinned = re.findall(r"trufflesecurity/trufflehog@[0-9a-f]{40}\s*#\s*v(\d+(?:\.\d+)*)", text)
    selected = re.findall(r"^\s*version:\s*\"?(\d+(?:\.\d+)*)\"?\s*$", text, flags=re.MULTILINE)

    assert pinned, "could not read the trufflehog action pin and its `# vX.Y.Z` comment"
    assert selected, (
        "no `version:` input on the trufflehog step. Without it the action defaults to "
        '"latest" and the SHA pin above it pins nothing that actually scans.'
    )
    assert len(pinned) == len(selected), (
        f"{len(pinned)} pinned trufflehog ref(s) but {len(selected)} `version:` input(s); "
        "every trufflehog step needs its own pinned version"
    )
    for ref_version, input_version in zip(pinned, selected, strict=True):
        assert ref_version == input_version, (
            f"the action is pinned to v{ref_version} but `version: {input_version}` is what "
            f"downloads the scanner, so the scan would run {input_version} and the bump to "
            f"v{ref_version} is a no-op. Set them to the same release."
        )


def test_checkout_keeps_full_history() -> None:
    """The precondition for a history scan, and not on its own the cause of one.

    `fetch-depth: 0` decides how much history `actions/checkout` puts on disk. What the
    scanner reads is decided by how it is invoked -- here, by this workflow running only
    on `schedule`/`workflow_dispatch`, the two events for which the action resolves its
    range to `--since-commit "" --branch ""`. Add a `push:` or `pull_request:` trigger
    and that stops being true while this assertion goes on passing, because the action
    would then take its range from the event. `ci.yml`'s gitleaks step was in exactly
    that shape, at full depth, scanning a pull request's own commits.
    """
    text = _workflow_text()
    assert "actions/checkout@" in text, "the scan no longer checks the repository out"
    assert re.search(r"^\s*fetch-depth:\s*0\s*(#.*)?$", text, flags=re.MULTILINE), (
        "`fetch-depth: 0` is missing from the checkout. actions/checkout then fetches a "
        "single commit and this full-history sweep silently becomes a one-commit scan "
        "that still reports success."
    )


def test_scan_walks_the_whole_repository() -> None:
    """`path: ./` is what stops the action resolving BASE == HEAD and scanning nothing."""
    text = _workflow_text()
    assert re.search(r"^\s*path:\s*\./\s*$", text, flags=re.MULTILINE), (
        "`path: ./` is missing; with path, base and head all unset the action exits on "
        'its own "BASE and HEAD commits are the same" guard having scanned nothing.'
    )
