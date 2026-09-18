# 0001. Single-maintainer branch-protection posture

Date: 2026-07-05

## Status

Accepted; the Python-version matrix provision is superseded by ADR 0004, and the
"no bypass actors" provision plus the "not yet applied" framing are corrected below
(see "Correction, 2026-08-29")

## Context

The audit (`audit-2026-07-05/women-artist-discovery-AUDIT.md`, P0-2) found a direct-to-main push
(commit `56628ee`, 2026-07-02) proving no merge gate was enforced — every AUTO gate in `ci.yml` was
advisory. The standard CI/CD posture (CICD-11 through CICD-18) assumes at least one independent
reviewer; this repo has exactly one maintainer, so "require ≥1 approving review" is unworkable —
it would either block all solo merges or force a meaningless self-approval.

## Decision

Adopt this posture for the `main` branch, expressed as a GitHub ruleset (not classic branch
protection), committed in `docs/audits/branch-ruleset.json`. This was written as a *target*
configuration; a ruleset of this name has since been applied and the framing is corrected
below (see "Correction, 2026-08-29"):

- **Require a pull request** to reach `main` — direct pushes are blocked, full stop.
- **Required approving review count: 0.** A PR is mandatory; a second human approver is not, since
  there isn't one. Review substance is enforced by the same person doing a deliberate PR-diff read
  before merging, not by a second reviewer.
- **All four `verify` matrix legs (Python 3.10–3.13) are required status checks**, strictly
  up-to-date with the base branch before merge.
- ~~**No bypass actors** — not even the repository owner/admin can merge around the ruleset. This is
  rulesets' equivalent of classic branch protection's `enforce_admins: true`; unlike classic branch
  protection, GitHub rulesets do not implicitly exempt admins, so an empty `bypass_actors` list is
  sufficient and was chosen deliberately over adding the owner as a bypass actor.~~
  **Wrong; corrected 2026-08-29 — see "Correction, 2026-08-29" below.** The premise is right and
  the conclusion is a lockout: because rulesets do *not* implicitly exempt admins, an empty
  `bypass_actors` list leaves nobody able to unblock the ruleset, including the person who would
  have to edit it. The posture is now the owner's standing admin bypass and nothing else.
- **Force-push and branch-deletion are blocked** (`non_fast_forward`, `deletion` rules).
- **Signed commits are not required yet** — see the alternative considered below.

## Alternatives considered

- **Require ≥1 approving review anyway**, satisfied by a second GitHub account or an external
  collaborator. Rejected for now: no second maintainer exists, and inventing one for compliance
  theater would be worse than an honest, documented single-maintainer posture.
- **Require signed commits immediately.** Rejected until commit signing (SSH or gitsign) is
  configured locally (tracked as P3, `CQ-41`/`REL-08`) — turning this on first would lock the
  maintainer out of their own repository.
- **Leave branch protection off entirely** (status quo). Rejected — this is exactly the P0 finding
  the audit raised; a documented, applied ruleset is strictly better even without a second
  reviewer.

## Consequences

- Every change to `main`, including the maintainer's own, must go through a PR with green required
  checks. This is a process cost accepted deliberately for the discipline it buys back.
- ~~`docs/audits/branch-ruleset.json` is the **target** configuration; applying it live requires a
  `gh api` call with `admin:repo` write scope, which the standards-remediation pass that produced
  this ADR deliberately did not execute (out of scope for an automated pass — see the remediation
  log for the exact command). Until it is applied, this ADR describes intent, not enforced fact.~~
  **True when written on 2026-07-05; no longer true. Corrected 2026-08-29** — a ruleset named
  `main-protection` is applied and active on `main`. See below.
- Once a second contributor exists, revisit `required_approving_review_count` and
  `require_code_owner_review` upward; this ADR should be superseded, not silently edited, when that
  happens.

## Correction, 2026-08-29

Two things this ADR says were true when it was written on 2026-07-05 and are not true now, and one
was never true. Nothing above is deleted; this section is what a reader should believe.

### 1. The ruleset is applied. It has been for a while

Measured read-only against the live API on 2026-08-29
(`gh api repos/ChelseaKR/lavender-rotation/rulesets/18752858`):

| Field | Live value |
| --- | --- |
| `id` | `18752858` |
| `name` | `main-protection` (the same name the committed file carries) |
| `target` / `conditions` | `branch`, `include: ["refs/heads/main"]`, `exclude: []` |
| `enforcement` | `active` |
| `created_at` | `2026-07-09T20:05:10.811-07:00` |
| `updated_at` | `2026-08-26T21:27:34.607-07:00` |
| `rules` | `non_fast_forward`, `deletion`, `required_status_checks`, `pull_request` |
| required contexts | `verify (3.12)`, `verify (3.13)`, `strict_required_status_checks_policy: true` |
| `bypass_actors` | `RepositoryRole:5:always` **and** `User:3114598:pull_request` |
| `current_user_can_bypass` | `always` |

`gh api repos/ChelseaKR/lavender-rotation/branches/main/protection` returns
`404 Branch not protected`, so this ruleset is the only thing enforcing anything on `main`; there is
no classic branch protection behind it.

The required contexts are real. `.github/workflows/ci.yml` defines one job, id `verify`, with no
`name:` of its own, over the matrix `python-version: ["3.12", "3.13"]`, triggered on
`pull_request: branches: [main]`. A matrix job reports one check run per combination and an unnamed
job reports under its id, so that workflow produces exactly `verify (3.12)` and `verify (3.13)` on
every pull request against `main`. Neither required context is orphaned.

### 2. "No bypass actors" was never safe, and the committed file was a loaded gun

The file carried `"bypass_actors": []` until 2026-08-29. Posting it with the documented
`gh api -X POST .../rulesets --input docs/audits/branch-ruleset.json` would have returned `201` and
left `main` with no break-glass path at all: no merge, no push, and no way to edit the ruleset doing
the blocking without the very access it removes. This is not theoretical. The same mistake, made by
an automated pass elsewhere, took a recovery sweep across eighteen repositories.

The file now carries exactly:

```json
"bypass_actors": [
  { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
]
```

`RepositoryRole` 5 is admin. `bypass_mode` is `always` and must stay `always`. An internal CI/CD
standard (CICD-15) asks for `pull_request` instead; that is the subtle version of the same lockout,
because a bypass that only works inside a pull request is no use when the pull request is the thing
that is wedged. `tests/test_branch_ruleset.py` now fails on the empty list, on a missing or
unparseable file, on a non-list, on a list without the owner, and on `bypass_mode: "pull_request"`.

### 3. Where the committed file and the server still disagree

The live ruleset carries a **second** bypass actor the committed file does not:
`{"actor_id": 3114598, "actor_type": "User", "bypass_mode": "pull_request"}` — the owner's own user
account, in addition to her admin role. No ADR, PR, or commit in this repository records a decision
to add it, so it is an unreviewed widening of who may skip every rule. It is not what keeps the
owner safe today; `current_user_can_bypass: always` comes from the `RepositoryRole:5:always` entry.

The committed file deliberately does **not** record it. This file is not a description of the
server, it is a document that gets POSTed, and recording the entry here would re-assert an
unreviewed widening every time someone applies the file. So the file states the reviewed posture,
the disagreement is written down here rather than hidden, and reconciling it is an owner-only server
change: either remove the `User:3114598:pull_request` entry live, or supersede this ADR with one
that says why it should stay.

Applying the committed file as it now stands would remove that second entry, which is a real server
mutation and is why applying it is a deliberate owner action, not a cleanup. The owner keeps
`RepositoryRole:5:always` either way, so no apply of this file can lock her out.

Four further fields are reported live that the committed file does not state:
`do_not_enforce_on_create: false` on `required_status_checks`, and `required_reviewers: []`,
`require_extra_approval_for_unattributed_changes: true`, `allowed_merge_methods: ["merge",
"squash", "rebase"]` on `pull_request`. This ADR does not claim to know where they came from; they
are recorded here as observed, not endorsed.

### 4. What an owner would run to reconcile it

Owner-only, and not run by the change that wrote this section. Read first, decide, then act:

```sh
# Read the live posture (safe, GET only).
gh api repos/ChelseaKR/lavender-rotation/rulesets/18752858 | jq '.bypass_actors, .rules'

# Only if the decision is to drop the unreviewed User:3114598:pull_request entry:
# apply the committed file, which asserts the owner's admin bypass and nothing else.
gh api -X PUT repos/ChelseaKR/lavender-rotation/rulesets/18752858 \
  --input docs/audits/branch-ruleset.json
```

`PUT` on the existing id, not `POST` to the collection — a `POST` creates a *second* ruleset named
`main-protection` alongside the live one, and two rulesets both apply. Whatever is applied, check
`current_user_can_bypass` afterwards and expect `"always"`; anything else means the break-glass path
is gone and the next merge is the one that discovers it.

### 5. The matrix provision, again

The Decision above says "all four `verify` matrix legs (Python 3.10-3.13)". ADR 0004 moved the floor
to 3.12, leaving two legs. Live and committed agree on the two that exist.
