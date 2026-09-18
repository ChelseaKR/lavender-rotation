# Per-axis refresh protection — working session of 2026-08-28

Companion to `docs/plans/improvement-plan.md`, which is the sibling clone's
log for the same day and the same GitHub repository. Two agents worked this
project in parallel from two local clones, blind to each other; both logs are
kept because each records ground the other did not cover.

> **Reconciliation note, added when this work was landed.**
>
> This document is the session's own log, written before either clone was
> committed, and it describes more work than this branch carries. It is kept
> unedited below that line because a working log that is quietly trimmed to
> match what shipped stops being evidence of what was looked at.
>
> What this branch actually lands:
>
> - **P0**, the per-axis `RefreshOutcome` fix (`_preserve_unanswered_axes`).
>   Unique to this clone, and this branch's own subject.
> - **The reconcile half of #93**, `normalize_asserted_orientation` and
>   `_same_claim`'s orientation leg, which the sibling branch does not cover.
>
> What was dropped, because PR #95 from the sibling clone
> (`/Users/chelsea/portfolio/lavender-rotation`, working the same GitHub
> repository from `main`) already covers it: **#92**, **#82**, the
> `_identity_sources` half of **#93**, the `i18n-gate.sh` / `check-staleness.sh`
> repair, the `writeup-check.py` hardening, the export-schema and no-inference
> guard repairs, and the README/Makefile edits. This branch is **based on PR
> #95's branch** rather than on `main`, so `pipeline/ingest.py` has exactly one
> version of the #93 fix and the two changes stack instead of conflicting.
>
> What was found here, is **not** covered by PR #95, and is **not** in this
> branch, recorded so it is not lost: `DANGEROUS_READS` in
> `tests/test_no_inference.py` omits `name` (the signal the README names first),
> `secret-scan.sh` scanned six extensions and so could not see a credential in
> any tracked `.json` or `.html`, `codeql.yml` defaults a missing SARIF glob to
> zero findings and passes, `conftest.py` uses `os.environ.setdefault` so an
> exported `LAVENDER_DATA_DIR` silently disables test isolation, `make a11y`
> degrades to the no-contrast fallback without saying so, and
> `test_committed_dashboard_cites_only_locatable_records` iterates a
> possibly-empty split. Each is described in "Gate audit (P4)" below.

Last verified: 2026-08-28

Working notes from a session in the second local clone. This file is the durable
record: the session ran under an explicit no-commit constraint, so every change
described here lives in the working tree only, and this document is the only
thing that survives an interruption.

## Read this first: what this checkout actually is

`/Users/chelsea/portfolio/women-artist-discovery` is **a second clone of
`github.com/ChelseaKR/lavender-rotation`**, not a separate project. It is on
`feat/live-refresh-that-cannot-erase-a-citation` at `ab91850`, which was already
squash-merged to `main` as PR #90 (`42cc5dc`). Remote `main` has since moved to
`bc61085` — PR #91 (lockfile drift gate) and PR #94 (pip 26.1.2 -> 26.2.1,
merged 2026-08-27). This clone is behind by those commits.

The session began from a stated premise that this was "the most neglected repo
in the portfolio, last commit 2026-08-18, ten days older than anything else."
**That premise was wrong**, and was corrected mid-session by the orchestrator.
The 2026-08-18 date is this stale branch's date, not the project's; `main` is
current and actively worked. A second agent was working the same GitHub repo
from the sibling clone at `/Users/chelsea/portfolio/lavender-rotation`, which is
on `main`.

Nothing here was *derived* from the false premise — no conclusion rested on the
date. The one thing that looks like rot was measured, not inferred, and was
correctly diagnosed as a stale-checkout artifact at the time (see below). But
the premise did set the **scope** too wide for the first part of the session,
and that is recorded honestly in "Scope, and what is now out of it".

## Constraint in force

Commit permission was withheld for the duration. Nothing was committed, staged,
stashed, pushed, fetched, rebased or merged; no branch was checked out and no
pull request was touched. A large unstaged diff is the intended end state.

## State the checkout was found in

- **`make verify` exits 2**, failing at stage 4 (`security`): `pip-audit`
  reports PYSEC-2026-3721 against pip 26.1.2. This is *already fixed on `main`*
  by PR #94 and is an artifact of the stale checkout, not live rot. `uv.lock`
  was deliberately **not** touched here — duplicating a merged fix in an
  uncommitted tree would only create a conflict for whoever reconciles this.
- Every other stage passes: ruff format + lint, cffconvert, the staleness gate,
  `mypy --strict` over 42 modules, 756 tests at 95.82% line coverage,
  `pipeline/identity.py` at 99%, the README claim check, a11y (pa11y/axe over
  three renders), the multiworld eval, and the i18n gate. Stages after
  `security` were run individually and all exit 0.
- Nothing is broken end to end. `make dev`, `lavender recommend`, the exports
  and the static render all work. **"It still works" is the honest headline.**

## Scope, and what is now out of it

This branch is about one thing: *a refresh must not read silence as agreement*.
Work that belongs to it, on its own merits:

- **#93** and the per-axis erasure found while fixing it (below). Both are
  defects in exactly this branch's code path and invariant.

Work done before the premise was corrected, which is **coherent and tested but
outside this branch's subject** and overlaps whatever the sibling agent is doing
on `main`. Kept rather than reverted, per instruction, and flagged here so the
owner can take or drop each piece independently:

- **#92** (queer-lens evidence in the why-cards, P91 edit link) — a different
  issue on the rendering surfaces.
- **#82** (eval fixture pools) — repo-level eval work.
- **The gate repairs** (`tests/test_no_inference.py`, `test_committed_render.py`,
  `test_export_schema.py`, `test_unknown_first_class.py`, `scripts/*.sh`,
  `scripts/writeup-check.py`, `Makefile`, `.github/workflows/*`, `conftest.py`)
  — repo-level, and the `Makefile` and workflow edits are the most likely to
  collide with the sibling clone.
- **README and CHANGELOG** edits — will conflict on any reconciliation.

## Findings, ranked by value

### P0 (in scope) — a partial upstream answer erased the axis it was silent about

Found while fixing #93, and it is this branch's own invariant with a hole in it.
`RefreshOutcome` protects an artist upstream said nothing about *at all*. It did
not protect an artist upstream answered **partly** about, and that is reachable
through an ordinary outage rather than a contrived one:
`MusicBrainzEnricher.gender_evidence` reads a gender claim straight out of the
MusicBrainz payload, while `orientation_evidence` needs a *second* fetch of the
Wikidata entity and `_json` renders any failure there as `None`. Wikidata down
while MusicBrainz is up therefore yields an artist carrying a fresh gender
citation and nothing on the queer axis — which `_is_sourced` reads as "upstream
answered", writes, and thereby erases the P91 citation. And because
`diff_identity_sources` walks the *new* sources, an emptied axis has nothing to
report: erasure plus a clean bill of health, which is #90's exact pairing one
axis down. Fixed per axis in `_preserve_unanswered_axes`.

### P1 (in scope) — Issue #93: `lavender refresh` silently drops a newly-sourced queer citation
`pipeline/ingest.py::_identity_sources` collects `identity.sources` and
`composition.sources` but never `queer.sources`, while `_is_sourced`'s docstring
claims it covers "either identity axis". Two consequences, both as filed:
an artist whose only new upstream evidence is a P91 orientation claim takes the
`unverified` branch and is never written; and `diff_identity_sources` cannot see
the queer axis move, so `corrections.reconcile_after_refresh` can never
reconcile a filed correction on that axis. Highest value: it is data loss on the
axis the project's own ethics audit calls the most dangerous.

### P2 (out of this branch's scope) — Issue #92: the queer lens's evidence is never shown
`recommender/explain.py` builds `identity_sources` from the gender axis or the
composition axis and nothing else, so `WhyThisArtist.provenance` — the one place
every surface reads citations from — cannot contain an orientation or trans
citation. A queer-lens boost is therefore justified to the reader by a *gender*
citation. `recommender/upstream.py::upstream_edit_url` also has no
`wikidata-p91` case, so the fix-at-source loop is closed for P21 and open for
P91. ADR 0011 says the queer axis's defense is "sourced-only with a citation"
being load-bearing; a citation nobody can see is not load-bearing.

### P3 (out of this branch's scope) — Issue #82 remainder: four of five eval worlds cannot vary recall
PR #84 fixed the *reporting* half (`recall_discriminates`, `recall_pinned_worlds`,
`n_worlds_recall_discriminating`, and a strict `aggregate_beats = mean_map_delta > 0`).
The committed report still reads `n_worlds_recall_discriminating: 1`. The
fixture half of the issue — "either the four fixture worlds grow a rankable pool
larger than `k`, or `k` drops below the pool" — is untouched: each of the four
`pipeline/fixtures.py` worlds offers exactly 4 candidates against `k=5`, so
recall is a tautology there for any ranker at all. The report's own
`recall_caveat` tells you to fix this.

### P4 (out of this branch's scope) — Gates that cannot fail
Audited specifically for the shape "present, green, structurally incapable of
reporting what it exists to report". Findings and repairs are logged below.

### P5 (out of this branch's scope) — Documentation drift
Claims checked line by line against behavior. The only measured drift was the
README's "756 tests at 96% coverage" line, which the repo's own
`scripts/check-readme-claims.py` catches; it is now 787.

### P6 — Issue #54: multi-platform export — **blocked, no work done**
The `Exporter` protocol seam the issue asks for already exists
(`export/base.py:138`) and both shipped adapters are conformance-tested against
it. Tidal ships (`export/tidal.py`, 23 tests). Apple Music needs a paid Apple
Developer Program membership and Qobuz needs partner approval: both are gated on
a credential no offline session can obtain, and writing an adapter against an
API surface that cannot be verified would mean inventing a specification. Left
open, untouched.

## Gate audit (P4) — what was found

Two read-only audits swept every gate script, every workflow, and the guard
tests. Findings, in the order they matter:

1. **`tests/test_no_inference.py` did not cover the signal the README names
   first.** `DANGEROUS_READS` holds `tags`, `genre`, `voice`, `image` and six
   more — and not `name`. A `_gender_from_name()` helper called from
   `resolve_identity` scans clean. Proved by inserting exactly that into
   `pipeline/identity.py`: leg 3 passed it.
2. **`test_no_identity_object_is_constructed_outside_the_pipeline` measures
   nothing.** It greps `recommender/`, `app/`, `export/` for identity
   constructor calls; there are zero today, and nothing asserts a non-empty
   population. `Path("does_not_exist").rglob("*.py")` returns an empty iterator
   without complaining, so renaming a package leaves the test green.
3. **`IDENTITY_CONSTRUCTORS` is a hardcoded string set nothing proves is live.**
   Rename `IdentityLabel` and every scan in the file keeps running, keeps
   finding nothing, and keeps reporting success — the same shape the file
   already closed for its exemption list and left open here.
4. **`test_committed_dashboard_cites_only_locatable_records` iterates a
   possibly-empty split.** The Wikidata half already iterates zero chunks. A
   renderer change to the link shape empties both halves silently, next to a
   byte-equality test that passes because the artifact is regenerated in the
   same change. It also never checked the Discogs citation the page carries,
   which is the one shape `citation_problem`'s by-host layer was written for.
5. **`docs/I18N.md`'s `Declared:` / `Reviewer:` line was checked by nothing.**
   `check-staleness.sh` exempts the file from the currency gate *on the stated
   grounds that `i18n-gate.sh` checks that line*. No version of `i18n-gate.sh`
   has ever mentioned `Declared` or `Reviewer`.
6. **`secret-scan.sh` scanned six extensions.** `py toml yml yaml sh md` — so a
   credential in any tracked `.json` or `.html` was invisible, and this repo
   tracks `docs/audits/*.json` plus two large committed `.html` artifacts. The
   header also claimed the fallback gives "the same merge-blocking guarantee" as
   gitleaks with the direction backwards: gitleaks is not in `ubuntu-latest`, so
   CI has always taken the weak path.
7. **`make a11y` degrades silently.** With `pa11y` absent it falls back to
   `app/a11y_check.py`, about ten structural rules and no color-contrast rule —
   which is the entire reason the light/dark scheme-pinned renders exist. On
   that path the three renders produce byte-identical results. Nothing asserted
   which runner ran.
8. **`writeup-check.py` never ran in CI**, and two of `methods.md`'s own
   annotations were shaped so its value regex could not reach them. They read to
   a human exactly like the checked ones.
9. **`check-staleness.sh` loops over a possibly-empty list** and falls through
   to its success message.
10. **`conftest.py` uses `os.environ.setdefault`** for the test data-directory
    isolation, so an exported `LAVENDER_DATA_DIR` silently disables it.
11. **`codeql.yml` swallows a failure**: `count=$(jq ...)` then `${count:-0}`
    means a missing SARIF glob defaults to zero findings and passes.

## Log

- [x] Baseline established: `make verify` exit 2, single failure = pip advisory
      already fixed upstream. All other stages green.
- [x] Plan recorded.
- [x] **P1 (#93)** — `pipeline/ingest.py::_identity_sources` now covers all three
      sourced axes, deduplicated; `_is_sourced` docstring corrected;
      `pipeline/identity.py` gained `normalize_asserted_orientation` and
      `pipeline/corrections.py::_same_claim` consults it. 6 new tests. Proved by
      reverting both fixes: 4 tests fail, then pass again.
- [x] **P2 (#92)** — `Explanation.queer_sources`, `WhyThisArtist.queer_statement`
      / `queer_provenance`, `recommender/why.py::artist_queer_phrase`, rendering
      in `to_text`/`to_markdown`/`app/render.py`/`app/dashboard.py`, and a
      `wikidata-p91` case in `upstream_edit_url`. Shown unconditionally, per
      ADR 0011 §4's own words. 10 new tests.
- [x] **Export egress gate extended to the queer axis**, with an explicit
      anti-vacuity test: the demo world ships no orientation evidence, so the new
      tokens would have been unfalsifiable against it. Uses an invented artist
      instead, and asserts the why-card *does* render the claim the export drops.
      Proved by leaking `queer_statement` into the track `why` field.
- [x] **P3 (#82 remainder)** — every eval world's rankable pool now exceeds `k`;
      `n_worlds_recall_discriminating` went 1/5 to 5/5. New gate
      `test_every_shipped_world_can_actually_measure_recall`, proved by shrinking
      a pool back. Repaired `test_the_denominator_travels_with_the_headline_recall_number`,
      which the fix would otherwise have turned vacuous.
- [x] **P4.1/P4.2/P4.3** — leg 3b (`taint_scan`) added to `test_no_inference.py`;
      constructor names asserted live; the outside-the-pipeline scan given a
      population floor. All three proved by breaking.
- [x] **P4.4** — committed-render citation check given a minimum-URL floor and
      taught the Discogs shape. Proved by removing the citation anchors.
- [x] **P4.5** — `i18n-gate.sh` now checks the `Declared:` / `Reviewer:` line.
- [x] **P4.6** — `secret-scan.sh` scans every tracked file (`grep -I` for
      binaries), refuses an empty file list, announces which scanner ran, and
      honors `LAVENDER_REQUIRE_GITLEAKS=1`.
- [x] **P4.7** — `make a11y` announces its runner and honors
      `LAVENDER_A11Y_REQUIRE_AXE=1`, set in `ci.yml`.
- [x] **P4.8** — `writeup-check.py` regex fixed (10 checked claims to 12), given
      an annotation-site pairing check, and wired into `make eval` so it runs
      under `make verify`.
- [x] **P4.9** — `check-staleness.sh` refuses a short file list.
- [x] **P4.10** — `conftest.py` assigns `LAVENDER_DATA_DIR` instead of
      `setdefault`, and announces an override.
- [x] **P4.11** — `codeql.yml` refuses to report a clean scan with no SARIF.
- [x] **P0 (in scope)** — `_preserve_unanswered_axes` makes the refresh
      protection per-axis. Proved both ways: reverting it lets a silent Wikidata
      erase a P91 citation, and over-correcting it (always keep the cached axis)
      is caught by `test_an_axis_that_did_answer_is_still_written`, so the guard
      cannot be satisfied by a refresh that never refreshes.
- [x] README test-count claim updated 756 -> 787; CHANGELOG entries added.
- [x] Premise correction recorded; scope reduced to the branch's own subject.

## Final gate state

- `make verify < /dev/null; echo "EXIT=$?"` -> **EXIT=2**, failing only at stage
  4 (`security`) on PYSEC-2026-3721 against pip 26.1.2 — the stale-checkout
  artifact described above, already fixed on `main` by PR #94.
- With pip 26.2.1 installed **into `.venv` only** (gitignored; `uv.lock`
  untouched), the same command is **EXIT=0**: "✓ all checkable gates green",
  793 tests at 96% coverage, `pipeline/identity.py` at 99%, axe over three
  renders, the multiworld eval with no regression against
  `docs/audits/eval-baseline.json`, `writeup-check` matching 12 claims, and the
  i18n gate. The venv was then restored with `uv sync --frozen` so the
  environment matches the lockfile again, which is why the committed state is
  EXIT=2.
- Nothing is staged, committed or pushed. `docs/audits/dashboard.html` is
  byte-unchanged: the new queer-provenance block renders only when a queer
  citation exists, and the demo world ships none.

## What remains

- **Rebasing this clone onto `main`.** HEAD-moving, and withheld. Until it
  happens `make verify` exits 2 here on a pip advisory already fixed upstream.
- **#54's Apple Music and Qobuz adapters** — externally gated on a paid
  membership and a partner approval respectively.
- **The manual screen-reader and keyboard sign-offs** the README already lists
  as review-gated.
- **Reconciling the out-of-scope work above** with whatever landed on `main`
  from the sibling clone.
