# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/); this project
intends to adhere to [Semantic Versioning](https://semver.org/) once a first version is tagged.

**Release stance:** this is unreleased pre-1.0 development software — see `SECURITY.md` and
`CITATION.cff`. There is no tagged release yet, so everything below lives under `[Unreleased]`.
When `v0.1.0` is tagged, its entries move to a new `## [0.1.0] - YYYY-MM-DD` section dated to the
tag, not backfilled to an earlier commit date.

## [Unreleased]

- Release authorization now runs from reviewed `main` through the immutable
  portfolio authorizer, builds and signs the exact selected commit, and hands
  only verified assets to a checkout-free publisher that rechecks the tag
  object immediately before creating the release.

### Security
- Update the transitive GitPython lock from 3.1.50 to 3.1.55, clearing the
  high-severity joined-short-option clone bypass fixed after 3.1.50.
- Update the transitive GitPython lock again, 3.1.55 -> 3.1.58, clearing two advisories
  disclosed after the prior bump (GHSA-3f7w-8rr8-f37f, GHSA-p538-c434-8v24; fixed upstream in
  3.1.56/3.1.57 respectively). Found via `pip-audit` while verifying an unrelated PR; fixed here
  as a minimal, separately-committed companion change rather than folded into that PR's diff.

### Added
- Verified-subject ledger for every identity citation the demo ships
  (`tests/test_demo_citations.py`): each citation is pinned to the subject a human read off the
  live registry, with the date. `citation_problem()` can only check that an identifier is
  well-formed; it cannot check that the record is the artist it was cited for, which is how three
  wrong Wikidata entities shipped. A citation absent from the ledger now fails, so an unverified
  identifier cannot be added silently, and the six `example.org` placeholders that stand in for
  real people's self-identification are enumerated rather than invisible.
- Docs-currency guard (`scripts/check-readme-claims.py`, wired into `make test`): re-derives the
  actual test count (`pytest --collect-only`) and coverage total (`coverage report
  --format=total`) and fails if either drifts from README's "Project status" claim, instead of
  that sentence silently going stale as the suite grows. Closes the "M8 auto-stamp backlog item"
  PR #49 flagged as open ("the README build-blockquote test count is not bumped here"); same
  "claims must be regenerable, never hand-typed" discipline `scripts/writeup-check.py` already
  applies to `docs/writeup/methods.md`.
- Make the internationalization row's N/A reason part of the machine-readable
  conformance status instead of leaving the table parser with a bare exemption.
- Mutation-testing gate on the safety-critical modules (CQ-47): `make mutation` runs cosmic-ray
  over `pipeline/identity.py` (no-inference) and `recommender/rerank.py` (boost-only), executing
  the full unit suite against every generated mutant, and fails if fewer than 70% are killed per
  module (`scripts/mutation-gate.sh`, `scripts/mutation/*.toml`). Runs weekly + on demand in CI
  (`.github/workflows/mutation.yml`) rather than nightly — a deliberate lean-Actions trade,
  documented in the workflow. The first run measured identity at only 62.3% killed **despite
  100% branch coverage** — exactly the coverage-vs-assertion-strength gap CQ-47 names — so this
  change also hardens `tests/test_identity_model.py` with exact-semantics tests (priority order
  under conflict, per-kind confidence arithmetic, filter/guard paths). Measured after hardening:
  identity 107/122 killed (87.7%), rerank 43/44 (97.7%); the survivors are equivalent mutants
  (enum `is`→`==`, unreachable dict defaults, order-preserving sort-key transforms, no-op
  rounding widths) plus one weakened defensive `assert delta >= 0.0` whose condition never fires
  precisely because the boost-only invariant holds upstream.
- Browser-driven accessibility specs (`tests/test_e2e_a11y.py`, A11Y-02/07/08/09): Playwright
  drives real Chrome over the static render and asserts keyboard completeness (skip link first
  and working, every interactive element reached in DOM order, no trap, 3px focus visible),
  320 px reflow (no page-level horizontal scroll), and reduced-motion (the
  `prefers-reduced-motion` override ships and nothing animates in either preference state).
  They auto-skip locally without a Chrome/Chromium; CI sets `WAD_E2E_REQUIRE=1` so a missing
  browser fails there instead of silently weakening the gate. Lighthouse CI remains absent, and
  the manual screen-reader walkthrough sign-off (M5) remains pending and human-only.
- `wad --log-format json`: opt-in JSON log lines on stderr, carrying the same fields as the
  `key=value` default; logging remains stderr-only with no network sink either way. Makes the
  README Observability claim true — the flag was documented before it existed.
- Merge-blocking no-identity-in-logs gate (`tests/test_log_privacy.py`, OBS-11): behavioural and
  AST-scan proofs that no log call site emits identity vocabulary, extending the no-inference
  invariant into the log stream.
- Playlist export: push recommendations to a Spotify playlist (OAuth Authorization Code flow,
  env-only credentials) or download a portable, account-free track list (plain text / CSV / M3U /
  JSPF) (#1).
- Shared `WhyThisArtist` explanation object, reused by the dashboard, static a11y render, CLI, and
  export, so identity/why wording cannot drift between surfaces (#1).
- `CITATION.cff` (#4).
- `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (#7).
- i18n `N/A` declaration (`docs/I18N.md`) plus a merge-blocking enforcement gate
  (`scripts/i18n-gate.sh`, CI `i18n` stage) (#8).
- Renovate (`renovate.json`) with `minimumReleaseAge: 72 hours` and GitHub Actions digest pinning
  (BL-8, 56628ee).

### Changed
- Recorded the supported Python floor of `>=3.12` in the remaining developer-facing
  surfaces (ADR 0004, which supersedes ADR 0002 and ADR 0001's four-version matrix
  provision; `CONTRIBUTING.md`; the committed main-ruleset target now requires only
  the supported `verify (3.12)`/`verify (3.13)` contexts). The floor itself landed
  in `pyproject.toml` via #51; this closes out the documentation and ruleset trail
  from #42.
- Migrated the Python floor from 3.9 to `>=3.10` (#6). Unblocked every dependency fix gated to
  Python ≥3.10 (see Security, below) and dropped Python 3.9 (EOL 2025-10-31) from the CI matrix.

### Fixed
- Three of the demo's Wikidata identity citations pointed at the wrong records. Mitski cited
  `Q16735549` (Andreas Constantinou, a Cypriot footballer, whose own P21 is male), Phoebe Bridgers
  cited `Q28907802` (a douar in Morocco), and Lucy Dacus cited `Q47545178` (a politician). Each was
  a well-formed Q-number that resolved, so `citation_problem()`'s shape check and any link checker
  passed them. Corrected to `Q23761694`, `Q24883319` and `Q27967785`, each confirmed against the
  live Wikidata API to be the named artist with `P21=Q6581072`.
- The demo's two `DISCOGS_LINEUP` citations were bare slugs (`/artist/big-thief`,
  `/artist/boygenius`), 22 lines after the fixture's own comment forbids exactly that shape.
  Discogs addresses artists by numeric id; corrected to `/artist/5009441-Big-Thief` and
  `/artist/6774153-boygenius`, both confirmed against the Discogs API including the member lists
  the band-composition claim rests on.
- `citation_problem()` now validates registry addresses by **host** as well as by source kind, so a
  citation claiming musicbrainz.org, wikidata.org or discogs.com has to be an address that registry
  can resolve, whatever kind carries it. `DISCOGS_LINEUP` was previously exempt by design, and it
  is the only source kind behind the demo's band-composition claims. Citations that claim no
  registry stay free-form, which was the original reason for the exemption.
- The demo-citation gate walked individual identity and band composition but not the front-person
  identities nested inside composition, reaching one level short of the data it covers.
- The committed `docs/audits/dashboard.html` is gated against the renderer (#71). It is a
  publicly browsable deliverable that nothing regenerated and nothing checked: `make a11y` ran
  `python -m app.build_static`, which **overwrote** the file and then audited the fresh copy, so
  the gate destroyed the evidence before looking, and `tests/test_e2e_a11y.py` builds into a
  tmpdir. On `main` the page still displayed three MusicBrainz citations that locate no record and
  their matching `/edit` links — the exact defect PR #66 fixed, on public display months later.
  `tests/test_committed_render.py` now asserts byte-equality with a fresh render (stage 3, before
  the a11y stage can overwrite anything), that the render is deterministic, and that every citation
  displayed locates a record; both new assertions were verified to fail against `main`'s copy.
  Regeneration moved out of `make a11y` into an explicit `make render` (also wired into
  `make audit`).
- Narrowed `[tool.coverage.run] omit` from `app/*` to `app/dashboard.py` (#71's related finding).
  The old justification — "Streamlit UI — verified via a11y + manual walkthrough" — held for
  neither: the a11y spec excludes the Streamlit dashboard in its own words and the walkthrough is
  still pending. It also hid three modules that are ordinary logic and measure 82–100%. Total
  coverage is unchanged at 97%; `app/dashboard.py`'s 0% is now recorded as an open gap in
  `docs/audits/accessibility-2026-07-17.md` instead of described as verified.
- `wad refresh` no longer deletes a filed pending correction when nothing upstream changed (#70).
  `corrections.reconcile` matched on `(artist_id, source_kind)` alone and never read
  `proposed_value`, while `ingest._diff_sources` emits a change when *either* the asserted value or
  the retrieval date moves — so a pure date refresh silently removed a person's note and printed
  "reconciled 1 pending upstream correction(s)". Reconciliation now requires the observed value to
  be the proposed one, compared through the controlled vocabulary (`identity.normalise_asserted_value`,
  so `"female"` reconciles a `"woman"` proposal); date-only changes reconcile nothing; a change to
  any other value marks the row **superseded** and keeps it on file with what upstream now asserts;
  and reconciliation only runs when an upstream source was actually queried, so the demo-only
  command reports `reconciled 0 … no upstream identity source was queried` instead of claiming an
  upstream that was never contacted. `reconcile` now returns a `ReconcileOutcome` naming every row
  it reconciled, superseded, or left open, and writes the operator summary itself — `pipeline/cli.py`
  is coverage-omitted as thin argparse glue, which is where the wrong status line was hiding.
  `tests/test_pending_corrections.py::test_reconcile_drops_matching_row_and_keeps_others`, which
  asserted the deletion was correct using a change whose old and new values were identical, is
  replaced by absence-of-harm tests plus an end-to-end CLI regression.
- The no-inference AST scan now covers the code that could infer (#72). It walked a hardcoded list
  of four function names in one file; `pipeline/identity.py` defines seven, nothing asserted the
  list covered the module, and a helper mapping genre tags to a gender — called from
  `resolve_identity`'s unknown fallback — passed it. The scan now walks every `def` **and
  `async def`** in every `pipeline/*.py` module, with no allowlist to fall off. Functions that
  legitimately handle content tags are named in `TAG_HANDLING_EXEMPTIONS` with a reason (a stale
  entry fails, so a hole cannot be pre-drilled) and are held to a stricter taint check: no value
  derived from a forbidden read may reach an identity constructor. The scan is extracted so it can
  be pointed at a synthetic bypass module and asserted to **fail** — a guard never shown failing is
  not evidence. `recommender/`, `app/`, and `export/` are now asserted to construct no identity
  objects, so an inference path cannot be introduced by relocating it. And
  `pipeline.identity.assert_permitted_only`, which had no caller outside its own test, is now
  invoked at the top of `resolve_identity` and `resolve_composition`, with a test proving it fires
  on the running path rather than only that its `if` works.
- The values lens's published harms note and the ranking now agree (#68). The note is rendered to
  the reader and promised that anyone unaligned was "never down-ranked, never treated worse than
  an unknown-identity artist"; the re-rank pinned only *unknown* slots, so an artist sourced as
  `Gender.OTHER` could be ranked below a **lower-scoring** unknown artist. Sourced `Gender.OTHER`
  is now rank-protected alongside `UNKNOWN` (`recommender/rerank.py::RANK_PROTECTED_GENDERS`),
  with a merge-blocking `assert_other_retained` counterpart to `assert_unknown_retained` and a new
  `assert_no_score_reduced` covering every artist of every identity; both are enforced by
  `wad eval`. The remainder of the promise cannot hold — a boosted artist that rises has to pass
  someone — so the note now states plainly that a sourced man's *position* can move down and that
  this is the whole of the lens's re-allocation. The score tables in both the dashboard and the
  committed static render gained a **Position** column so a reader can see why rank is not a pure
  function of total score. `tests/test_lens.py::test_lens_other_is_not_penalised_like_unknown`,
  which had asserted only that two boosts were both `0.0` and stayed green throughout the defect,
  now asserts the rank protection its name describes.
- A band whose only sourced front-person is a nonbinary artist is no longer described as a
  "female-fronted band" (#69). `BandComposition.female_fronted` now means only "a front-person's
  own sourced gender is `WOMAN`" and no longer reads the lens's `VALUES_ALIGNED_GENDERS`, so lens
  policy can never change what the data model asserts; the unflattened fact is the new
  `BandComposition.sourced_front_genders`, which every rendered label, the identity-coverage
  readout, and the fairness segmentation (new `nonbinary-fronted` segment) are written from. The
  lens keeps boosting such bands by intersecting its own aligned set with the sourced front
  genders, so no artist loses exposure to the fix. `tests/test_front_person_labels.py` asserts
  the absence of the harm across every surface `recommender/why.py` feeds.
- `mean_recall_delta` in the multiworld eval report was one world's number divided by five.
  Four of the five fixture worlds have a four-candidate rankable pool at the default `k=5`, so
  the top-k is the entire pool for every possible ordering and `recall_at_k` is 1.0 for any
  ranker — a perfect one, a random one, a reversed one — including the popularity baseline.
  Those four contributed a structurally-pinned `recall_delta` of 0.0, turning
  demo-tuned-indie's measured 0.5 into a published headline of 0.1. Recall is now reported
  per world with `recall_discriminates` and a note naming the pool size, `mean_recall_delta`
  averages only the worlds where recall could vary, and
  `n_worlds_recall_discriminating` sits next to it so the denominator is never implicit.
  The report's own caveat told readers to trust the aggregate over any single world; the
  aggregate's recall signal came from that single world.
- The eval verdict accepted a draw. `hybrid_beats_popularity` was true when MAP@k tied and
  recall was merely not worse — a condition an exact tie on every metric in every world
  satisfies, and one the recall half of which was pinned true anyway in the four worlds above.
  Both the per-world and aggregate rules now require a strict MAP@k improvement, which is what
  README's merge-blocking "the offline eval must beat the popularity baseline" says.
- OpenSSF Scorecard workflow comments now describe the repository's current
  public publishing path instead of its superseded restricted-publication
  posture.
- 320 px reflow defect caught by the new browser specs: the score-summary and fairness tables
  forced page-level horizontal scrolling at narrow widths (WCAG 2.2 §1.4.10). Data tables now sit
  in keyboard-focusable, labelled scroll regions (`role="region"`, `tabindex="0"`,
  `overflow-x: auto`) so only the excepted two-dimensional content scrolls — Arrow keys operate
  it, and the page itself reflows; long citation URLs additionally wrap (`overflow-wrap`).

### Security
- Declared `pillow>=12.3` explicitly in the `app` extra (PYSEC-2026-2253 through
  PYSEC-2026-2257), so the constraint no longer relies on Streamlit's transitive
  floor; `uv.lock` already resolved Pillow 12.3.0.
- `persist-credentials: false` on the CI checkout step, so the default `GITHUB_TOKEN` is not
  persisted for later steps (#4).
- All GitHub Actions `uses:` pinned to 40-character commit SHAs with version comments, closing the
  prior floating-tag supply-chain gap (#4, kept current by Renovate's digest pinning).
- Dependency security refresh: documented and waived the 19-advisory Python-3.9-EOL cluster
  (`requests`, `urllib3`, `streamlit`, `pillow`, `pyarrow`, `msgpack`, `filelock`, `pytest`, `pip`)
  with a committed, justified VEX (#5), then **resolved it outright** via the Python 3.10+
  migration (#6) — `pip-audit` now runs with an **empty** waiver list and no `--ignore-vuln` flags.
  See `docs/audits/residual-risk.md` (RR-1, RR-4) and `docs/audits/vex.json`.

---

*Older history predating 2026-06-29 is not available — the git history backing this repository was
reset on that date (see `docs/ROADMAP.md` and the audit trail in `audit-2026-07-05/` for context);
this changelog starts from the current history's initial commit forward.*
