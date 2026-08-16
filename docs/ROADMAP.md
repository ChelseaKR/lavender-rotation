# Women-Artist Discovery — Implementation Roadmap

> Generic enforcement lives in `/STANDARDS`. This document carries the decisions and project-specific values.
> **Last verified: 2026-07-05 · Recheck cadence: per Last.fm / MusicBrainz / Discogs / Wikidata API change, or per standards-conformance remediation pass.**

## 1. Snapshot
A hybrid Last.fm-driven music-discovery engine with a values-aware re-ranking layer and a sourced-not-inferred identity model. Python pipeline + Streamlit dashboard; local-first. The technical novelty is doing identity-aware recommendation responsibly — never guessing, always citing, treating unknown as normal.

## 2. Problem & users
- **Problem.** Recommenders are identity-blind by default; there's no good way to discover women/nonbinary/female-fronted artists on purpose without crude gender-guessing.
- **Primary user.** You (single-user, personal); secondarily, anyone who wants values-aware discovery done ethically.
- **Jobs to be done.** "Find new women/nonbinary/female-fronted artists I'll like." · "Don't make the tool guess anyone's gender." · "Tell me *why* each pick showed up."
- **Evidence basis.** Your own scrobble history is the ground truth; offline-evaluate recommendations on held-out listens.

## 3. Product definition
- **Vision.** Discovery that respects both taste and identity, without essentialism.
- **Scope (MoSCoW).**
  - *Must:* Last.fm ingest; enrichment (MusicBrainz/Wikidata/Discogs); hybrid recommender; values-aware re-rank; sourced identity model with unknown-first-class; per-recommendation explanation; dashboard.
  - *Should:* ~~playlist/export~~; ~~thumbs feedback to tune future rankings~~ (implemented). ListenBrainz collaborative signal is deferred pending a separate provider/privacy/live-data validation pass.
  - *Could:* acoustic/content features; ~~a "discovery report"~~ (done: `wad report`); additional sourced value lenses only after affected-community and sourcing review.
  - *Won't (v1):* inferring identity from any signal; redistributing an identity dataset; cross-user/social features.
- **Non-goals.** Not a gender database product; not identity-blind; not a guessing engine.

## 4. Research & evidence
- **Identity sourcing.** Define the *only* permitted identity sources and their provenance + confidence; document each one's limits (Wikidata P21 is sparse and sometimes wrong; MusicBrainz gender is editorial/self-reported; Discogs gives lineup, not individual identity). Default everywhere is **unknown**.
- **"Female-fronted" definition.** An operational, sourced lineup/role property (who fronts / who's in the band), explicitly *not* a claim about any member's gender.
- **Recommender baseline.** Validate the hybrid against a popularity baseline on held-out scrobbles before adding the re-rank.

## 5. Experience & design
- **Streamlit dashboard.** Enter username → ranked recommendations as "why" cards (signals + identity basis + sources), plus a respectful "unknown — surfaced on similarity" state.
- **Lens control.** A slider for how strongly the values lens re-ranks, always visible and explained.
- **Accessibility.** Keyboard-complete, charts have data-table equivalents, identity/why info never conveyed by color alone. Release gate.

## 6. Architecture
- **Shape.** Python pipeline (ingest → enrich → recommend → re-rank → explain) + SQLite cache + Streamlit UI.
- **Recommender.** Collaborative Last.fm similar-artist signal + content (tags/genres) hybrid; a re-rank layer applies *sourced* identity weights.
- **Identity resolver.** Pulls only from permitted sources, attaches source + confidence to every label, and defaults to unknown. There is deliberately **no** inference path.
- **Key decisions (ADRs).** Streamlit over React (solo speed, data-app shape; React noted as a later option). Hybrid over single-method (cold-start + serendipity). Identity = sourced-only with unknown-first-class (rejected: name/voice/image/genre inference — unethical and inaccurate). Cache + rate-limit respect (rejected: aggressive scraping).

### Build log (decisions made during implementation, 2026-05-31)
Per the Documentation Standard ("keep docs live"), decisions the plan didn't anticipate:
- **Guardrails as type invariants, not just tests.** `IdentityLabel`/`Source`/`BandComposition` in `pipeline/models.py` raise on any unsourced or mis-sourced identity, so a guardrail violation can't even be constructed. The no-inference test (`tests/test_no_inference.py`) adds an AST scan as a regression backstop — over every function in every `pipeline/` module since #72 (2026-08-14), which found it walking a hardcoded list of four names in one file and passing on a genre-inference path.
- **`female_fronted` is tri-state** (`True`/`None`), never `False` by inference — absence of a sourced woman front is "unknown", not "male-fronted". It is also narrow: it means *only* "a front-person's own sourced gender is `WOMAN`". The general fact is `BandComposition.sourced_front_genders`, which keeps every sourced gender as itself; a band fronted only by a sourced nonbinary artist is nonbinary-fronted, never "female-fronted" (#69, 2026-08-14).
- **Re-rank is boost-only with protected unknown slots** (`recommender/rerank.py`, `recommender/hybrid.py`): unknown scores and pure-taste ranks stay invariant, while aligned artists and identity-blind MMR reorder only sourced non-unknown slots. The full list is reconstructed before top-k selection. The boost remains bounded by `MAX_BOOST` so taste is preserved.
- **Coverage gate scoped to core logic** (`pipeline` + `recommender`, 94%); the Streamlit UI is verified by the a11y gate + manual walkthrough rather than unit coverage.
- **A11y gate** = static render (`app/render.py` → `docs/audits/dashboard.html`) checked by `pa11y --runner axe`, with a dependency-free `app/a11y_check.py` fallback for offline/CI-without-Chromium.
- **Performance/Lighthouse budgets N/A** for this local-first, single-user data app (no hosted LLM/API route) — recorded as residual risk RR-3.
- **Security:** `pip-audit` + ruff-bandit (`S`) + secret scan are merge-blocking. Accepted, tracked residual risks: RR-1 (CVE-2025-8869, pip tar extraction — build-tooling only) and RR-4 (a 19-advisory cluster whose fixes all require Python ≥3.10, so none is installable on the 3.9 floor — justified per-ID in `docs/audits/vex.json`, waived byte-identically in `Makefile`/`ci.yml`). See `docs/audits/residual-risk.md`.
- **Runtime:** supported Python versions are tested in the verification matrix; direct runtime dependency is `requests`, with `streamlit`/`pandas` in the app extra. The unused direct `numpy` dependency was removed under FIX-13.

### Build log addendum (2026-06-29) — playlist export + "Why this artist"
- **"Why this artist" centralised** in `recommender/why.py` (`WhyThisArtist`): one render-agnostic explanation — sourced identity statement, hybrid + values-lens reasons, and provenance that shows *the raw value each source asserted* (not just a label), plus an explicit `inferred = False`. The dashboard, the a11y static renderer (`app/render.py`), the CLI, and the export now share this single source of truth, removing the previously-duplicated identity wording (also reused by `recommender/explain.py`).
- **Playlist export** as a new top-level `export/` package — deliberately *outside* `pipeline`/`recommender` so the privacy test's "core network confined to `lastfm.py`" guarantee still holds and the export egress is a separate, opt-in boundary. Credential-free fallbacks (plain text / CSV / M3U / JSPF, `export/tracklist.py`) need no account; live Spotify (`export/spotify.py`) uses the Authorization Code OAuth flow with an injectable `HttpTransport` (fake in tests, `requests` only in `RequestsTransport`), credentials from env only. `wad export` CLI + dashboard download buttons + a Spotify connect panel. New egress documented in `docs/audits/privacy-notes.md`.
- **No new dependencies** (stdlib `base64`/`csv`/`json`/`secrets`/`urllib`; `requests` already present). Realised the roadmap "Should: playlist/export" item.
- **Needs real creds to run live:** a Spotify app + a browser OAuth consent; only `RequestsTransport` is uncovered (live network), exactly like `LastfmClient`.

### Build log addendum (2026-07-03) — scale the scoring path (FIX-13)
- **numpy dropped, not adopted.** It was declared but never imported; the tag scorer uses sparse dictionary operations, so a dense-array dependency increased the runtime and audit surface without helping this workload. The app extra may still bring it transitively through pandas.
- **`make bench` / `scripts/bench.py`** adds a seeded synthetic benchmark with 200 known artists, 5,000 candidates, and 50,000 scrobbles. It reports p50/p95 for collaborative, content, and end-to-end scoring without changing production randomness or ranking behavior.
- **Measured p95 was approximately 140 ms** end-to-end on the implementation machine, below the two-second target. Content scoring was the largest component, but the evidence did not justify candidate pruning or a more complex representation.

### Build log addendum (2026-07-11) — per-artist thumbs feedback
- Added local, per-listener thumbs votes in cache schema v4, exposed in the dashboard and `wad feedback`. A re-vote replaces the current opinion for that listener/artist pair.
- Feedback is a bounded adjustment to the taste-side base score. It is keyed only by listener and artist ID, never identity, so the values lens remains independently boost-only and its rank-shift counterfactual remains inspectable.

### Build log addendum (2026-07-02) — FIX-07: runtime egress guard across all packages
- **Done.** The "core network confined to `lastfm.py`" privacy guarantee was
  only ever a source scan over `pipeline`/`recommender`, so it missed the
  `app/` and `export/` packages added by the playlist-export work above, and
  it couldn't catch indirect/transitive network calls at all. Closed with two
  enforcement gates, documented as the single source of truth in
  `docs/audits/privacy-notes.md` ("Egress registry / allowlist"):
  1. **Source scan (gate 1)** — `tests/test_privacy.py::_core_files` now also
     walks `app/` and `export/`; `NETWORK_TOKENS` grew to cover indirect
     egress (`httpx`, `urllib3`, `aiohttp`, `webbrowser`); `NETWORK_ALLOWED`
     now names the exact `pipeline/lastfm.py` and `export/spotify.py` paths,
     matching `RequestsTransport`'s documented allowlist.
  2. **Runtime socket guard (gate 2)** — an autouse `_no_network` fixture in
     `tests/conftest.py` patches connection and datagram socket paths to raise
     for every test, proving the suite is
     offline by construction rather than by convention.
- **Verified:** `make test` is green (149 passed, 96% coverage on the gated
  `pipeline`+`recommender` scope, well above the 85% floor); a deliberately
  added `import requests` in `app/dashboard.py` was confirmed to fail the
  source-scan gate, then reverted.

### Build log addendum (2026-07-03) — EXP-02: rank-shift transparency — done
- Every recommendation records its pure-taste `base_rank` before the values
  lens is applied. Every shared why-card surface states whether the lens moved
  the pick and, if so, from which rank to which rank. Unknown-identity picks
  are test-asserted never to improve from a boost they did not receive.

### Build log addendum (2026-07-03) — EXP-11: shareable static discovery report
- `wad report` writes a self-contained HTML file with the same renderer and
  accessibility gate as the committed dashboard artifact. `--k`, `--lens`,
  and `--out` make it a user feature without adding a second rendering path.

### Build log addendum (2026-07-05) — standards-conformance remediation
Executed `audit-2026-07-05/women-artist-discovery-REMEDIATION.md` (see that file for the
control-by-control status). Highlights: README now carries a real Standards Conformance table
(replacing silent "Inherits /STANDARDS"); the phantom "0.1.x release" claim in SECURITY.md/
CITATION.cff corrected to an honest "unreleased pre-1.0" stance (`CHANGELOG.md` added); CI now
installs via `uv sync --frozen` against `uv.lock` instead of pip-from-floors (the lockfile is
finally what CI actually runs on); build backend moved setuptools → hatchling (closes CQ-10) with
a prepared (not-yet-triggered) tag-release workflow; CODEOWNERS + a target branch-ruleset artifact
committed (live application is a manual, human-authorized step — see the remediation log);
CodeQL/zizmor/osv-scanner/Scorecard workflows added. Nothing in the identity/fairness safety core
(`pipeline/identity.py`, `recommender/rerank.py`, `tests/test_no_inference.py`,
`tests/test_unknown_first_class.py`) was touched.

### Build log addendum (2026-08-04) — docs-currency guard for README's test/coverage claim
- **Closes an explicitly-flagged gap.** PR #49 (mutation-testing gate, M8/CQ-47) noted in its own
  description that "the README build-blockquote test count is not bumped here ... the M8
  auto-stamp backlog item is the systemic fix and remains open." By the time this landed the
  claim ("433 tests at 97% coverage") had drifted to 501 actual tests — silent staleness the repo
  had no gate against, exactly the "keep docs live" discipline this section already cites.
- **`scripts/check-readme-claims.py`**, run as the last step of `make test` (stage 3, right after
  pytest populates `.coverage`): re-derives the actual test count via `pytest --collect-only` and
  the actual coverage total via `coverage report --format=total`, and fails loudly if either
  differs from what README's "## Project status" paragraph currently states. Mirrors
  `scripts/writeup-check.py`'s existing "claims must be regenerable, never hand-typed and stale"
  pattern for `docs/writeup/methods.md`, applied here to the README's own gate summary.
- **Scope, deliberately narrow.** Only README's present-tense "## Project status" claim is
  checked. `docs/USER-RESEARCH.md`'s persona interview quoting "108 tests @ 94%" and this file's
  own build-log addenda quoting point-in-time numbers (e.g. "149 passed, 96% coverage" above) are
  dated, historical snapshots by design, not live claims — checking them would be a category
  error, not a fix.

## 7. Quality attributes & metrics
| Metric | Target | Measured by | Gate |
|--------|--------|-------------|------|
| Inferred identity labels (name/voice/image/genre) | 0 | labeling-pipeline test (asserts no inference path exists) | merge-blocking |
| Identity labels carrying a cited source | 100% | provenance test | merge-blocking |
| Recommendations down-ranked solely for unknown identity | 0 | re-rank test | merge-blocking |
| "Why recommended" present | 100% of recs | explanation test | merge-blocking |
| Recommendation reproducibility (seeded) | deterministic | snapshot test | merge-blocking |
| axe violations (dashboard) | 0 | pa11y-ci | merge-blocking |
| External API rate-limit compliance | within limits, cached | integration test | merge-blocking |
| Coverage | ≥ 85% / ≥ 80% | coverage | merge-blocking |
| Release stage | unreleased pre-1.0 (declared, not silent) | `SECURITY.md`, `CHANGELOG.md` | review-gated |
| Observability tier | Tier C (declared) | README `## Observability` | review-gated |
| AI-evaluation status | narrow-applies (declared); eval-beats-baseline active | `docs/RESPONSIBLE-TECH-AUDITS.md`, `make eval` | merge-blocking (eval half) |

**DORA note.** The git history was reset 2026-06-29 (see `audit-2026-07-05/women-artist-discovery-AUDIT.md` §3), so deployment-frequency/lead-time/change-failure-rate/MTTR cannot be measured before that date — pre-reset delivery evidence no longer exists in this clone. Measurement restarts from 2026-06-29: 8 commits landed 2026-06-29→2026-07-02 (dependency/security/docs remediation), then this standards-conformance pass on 2026-07-05. No production deploys or incidents exist yet (pre-release, personal project), so change-failure-rate/MTTR are not yet meaningful; commit cadence is the only DORA-adjacent signal available today. Revisit once releases exist.

**Testing.** Unit (identity resolver refuses inference; re-rank math; unknown handling), integration (Last.fm/MusicBrainz/Discogs/Wikidata adapters with cached fixtures), eval (offline recommender quality vs popularity baseline), a11y.

## 8. Implementation plan for Claude Code
```
pipeline/     (ingest, enrich, identity resolver)
recommender/  (collaborative, content, hybrid, re-rank, explain)
app/          (streamlit dashboard)
data/         (sqlite cache)
docs/
```
- **M0 — Scaffold & gates.** Repo + CI (`/STANDARDS` gates + axe + the no-inference test). *Done when `make verify` is green and the no-inference test exists and passes.*
- **M1 — Ingest + cache.** Last.fm scrobbles/tags into the cache. *Done when a username yields a stored listening profile.*
- **M2 — Identity resolver.** Permitted-source resolver with provenance + unknown default; the guardrail tests. *Done when labels carry sources and inference paths are provably absent.*
- **M3 — Recommender.** Collaborative + content hybrid. *Done when held-out eval beats a popularity baseline.*
- **M4 — Values-aware re-rank.** Sourced identity weighting; unknown never penalized. *Done when re-rank tests pass and unknown artists still surface.*
- **M5 — Dashboard + explanations.** Streamlit UI with why-cards + sources; a11y. *Done when every rec shows why + basis + source and axe = 0.*
- **M6 — Polish.** Feedback loop, export, discovery report. *Done when all §7 gates pass.*
- **Claude Code approach.** Write the no-inference guardrail test *first*; identity defaults to unknown everywhere; never let a missing label change a score.

## 9. Community & comms
- **Marketing/comms.** The writeup is the artifact: how to do values-aware recommendation *without* inferring identity. That's a rare, credible engineering-ethics story.
- **Community.** Contribution guide and reusable identity-data-ethics doc are shipped; a real upstream correction fold-back mechanism remains open (the current refresh CLI is demo-only).

## 10. Legal & compliance
- **API terms** (Last.fm, Discogs, MusicBrainz, Wikidata) honored; **no redistribution** of a scraped identity dataset; personal-use scope; MusicBrainz/Wikidata attribution.
- **Privacy.** Listening data is personal → local-first; no third-party analytics.

## 11. Operations & sustainability
- **Hosting/cost.** Runs locally or on a small host; cheap; the cache cuts API load.
- **Maintenance.** Cache TTL/diff primitives exist and live enrichment now uses them (`wad ingest --ttl-days`, FIX-01). What remains deferred is *periodic* re-enrichment (there is no scheduler; a re-check is an operator running the command again) and automatic source-correction fold-back — `wad refresh` still walks the fixture catalog rather than the live enricher, so a filed correction is still reconciled by nothing.
- **Sustainability.** Single-user, low cost, open methodology survives the maintainer.

## 12. Responsible-tech summary
Top risks: (1) misgendering/essentialism via inference → sourced-only + unknown-first-class + a real nonbinary model; (2) building a misusable musician-identity database → minimize, cite, correctable, no redistribution; (3) listening-data privacy → local-first; (4) a re-rank that quietly erases unknown artists → unknown never penalized (tested). Full treatment in [`RESPONSIBLE-TECH-AUDITS.md`](./RESPONSIBLE-TECH-AUDITS.md).
