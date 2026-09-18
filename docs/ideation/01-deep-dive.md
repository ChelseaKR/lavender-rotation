# Deep Dive — Current State (2026-07-01)

Assessment from a full read of the source, tests, Makefile/CI, and audit docs.
No test suites or network calls were run for this pass; where a claim depends on
runtime behavior, that uncertainty is stated.

## Architecture as actually built

Four packages, cleanly layered, all `py.typed`, `mypy --strict`:

- **`pipeline/`** — ingest and identity.
  - `pipeline/models.py`: the domain model where the guardrails live as
    **construction-time invariants**, not conventions. `IdentityLabel.__post_init__`
    raises `UnsourcedIdentityError` on any non-unknown gender without an
    individual-identity citation; `SourceKind` simply has no member for
    name/voice/image/genre; `BandComposition.female_fronted` is tri-state
    (`True`/`None`, never `False` by inference).
  - `pipeline/identity.py`: the resolver. Controlled vocab
    (`_FREEFORM_VOCAB`, `_WIKIDATA_QID_VOCAB` — trans-inclusive by explicit
    mapping), source priority (artist statement > Wikidata P21 > MusicBrainz),
    deterministic confidence, defaults to `UNKNOWN_IDENTITY`.
  - `pipeline/lastfm.py`: `ScrobbleSource` protocol with a live `LastfmClient`
    (rate-limited via `RateLimiter`, HTTP-cached) and `FixtureLastfm` for
    offline use. Pure, shape-validating parsers.
  - `pipeline/enrich.py`: pure parsers for MusicBrainz gender / Wikidata P21 /
    Discogs lineup → `IdentityEvidence`, plus `FixtureEnricher`.
  - `pipeline/cache.py` + `pipeline/serde.py`: local SQLite with `fetched_at`
    lineage on every row; deserialization re-runs the model invariants so a
    corrupted cache row fails closed.
  - `pipeline/demo.py`: a hand-built offline world spanning every identity
    basis (sourced women, a sourced nonbinary artist, sourced female-fronted
    bands, sourced men, first-class unknowns).
- **`recommender/`** — `collaborative.py` (play-weighted similar-artist graph),
  `content.py` (tag-cosine), `hybrid.py` (convex blend, min-max normalized),
  `rerank.py` (**boost-only** values lens, `MAX_BOOST = 0.5`, with an inline
  `assert delta >= 0.0`), `explain.py`/`why.py` (the shared `WhyThisArtist`
  object with raw asserted values in provenance), `eval.py` (temporal split,
  precision/recall/MAP@k vs a popularity baseline).
- **`app/`** — `dashboard.py` (Streamlit), `render.py` (accessible static HTML,
  the artifact the a11y gate audits), `build_static.py`, `a11y_check.py`
  (dependency-free fallback checker).
- **`export/`** — deliberately outside `pipeline`/`recommender` so the privacy
  test's "core network confined to `lastfm.py`" claim stays true.
  `tracklist.py` (text/CSV/M3U/JSPF, credential-free), `spotify.py` (OAuth
  Authorization Code flow behind an injectable `HttpTransport`).

**Tests:** 16 files, 125 test functions counted by grep (collected count is
higher due to parametrization — e.g. `tests/test_no_inference.py` parametrizes
over 17 forbidden tokens; the README's "108 tests" figure is likely stale).
The suite covers the guardrails from four angles: vocabulary, structure, AST
scan, and behavior (`test_no_inference.py`), plus unknown-retention
(`test_unknown_first_class.py`), provenance, serde fail-closed, privacy source
scans, reproducibility snapshots, export, and a11y.

**Gates:** `Makefile` `verify` = lint (ruff incl. bandit subset) → `mypy
--strict` → pytest (≥85% branch-aware coverage on `pipeline`+`recommender`+
`export`) → pip-audit + secret scan → a11y (pa11y/axe with offline fallback) →
eval (must beat popularity baseline) → i18n N/A gate. `.github/workflows/ci.yml`
mirrors it across Python 3.10–3.13 with SHA-pinned actions and
`persist-credentials: false`.

## What is genuinely strong

1. **Guardrails as types, not tests.** A misgendering label is
   *unconstructible* (`pipeline/models.py`), and the AST scan in
   `tests/test_no_inference.py` backstops the resolver against future
   regressions. This is the most credible "ethics as engineering" artifact in
   the repo and arguably the portfolio's cleanest example of the pattern.
2. **The boost-only rerank makes the fairness claim mechanical.**
   "Unknown never penalized" is not a policy; it is arithmetic
   (`recommender/rerank.py::values_boost_for_artist` returns 0 or a bounded
   positive number).
3. **Single source of truth for explanation wording.** `recommender/why.py` is
   shared by the dashboard, static renderer, CLI, and exports, so the identity
   phrasing cannot drift between surfaces.
4. **Egress discipline is real.** Exactly two outbound paths, both isolated
   behind single injectable transports (`pipeline/lastfm.py`,
   `export/spotify.py::RequestsTransport`), documented in
   `docs/audits/privacy-notes.md` and partially enforced by
   `tests/test_privacy.py`.
5. **Honest audit hygiene.** The residual-risk register tracks resolutions
   with dates (RR-1/RR-4 closed by the 2026-06-30 Python 3.10 migration); the
   a11y audit openly marks the manual screen-reader walkthrough as pending.

## Structural debt and gaps actually observed

1. **The live path is a façade.** `app/dashboard.py` (lines 135–137) always
   calls `_load_demo()` — even with `WAD_LASTFM_API_KEY` set it prints "Live
   mode would fetch this user; this demo build uses cached data".
   `pipeline/cli.py` likewise only ever uses the demo world. More
   fundamentally, **no live enrichment client exists**: `pipeline/enrich.py`
   has parsers and a `FixtureEnricher` but no class that actually fetches from
   MusicBrainz/Wikidata/Discogs. The README's definition of done ("from your
   Last.fm username, the app returns explainable recommendations") is not
   reachable from the shipped code. `LastfmClient` exists but nothing
   constructs it end-to-end.
2. **Ingest doesn't scale past a demo.** `LastfmClient.recent_scrobbles` makes
   a single call (`limit=200`, no pagination, no incremental sync);
   `Cache.put_scrobbles` blindly INSERTs with no dedupe key, so repeat ingests
   would duplicate rows; the HTTP cache has no TTL, so a stale response is
   cached forever — despite RR-2's "corrections fold back via re-enrichment"
   there is no re-enrichment command.
3. **Artist identity keying is fragile.** `parse_recent_tracks` keys artists on
   `mbid or name`; many Last.fm artists lack MBIDs, so the same act can appear
   under different keys across the scrobble, similarity, and enrichment paths.
   There is no entity-resolution layer.
4. **No computed fairness metric.** `docs/audits/fairness-identity.md` argues
   the fairness case well, but `recommender/eval.py` computes only
   precision/recall/MAP. Nothing measures exposure by identity basis or rank
   shift under the lens; "popularity-debiasing check" exists as prose (and the
   `Artist.listeners` comment in `pipeline/models.py:228`) rather than as a
   number in `eval-report.json`.
5. **The eval is circular.** `pipeline/demo.py`'s docstring says the demo world
   is "tuned so the hybrid recommender recovers genuine held-out discoveries
   that a popularity baseline misses" — the CI eval gate therefore verifies a
   fixture designed to pass it. Honest as a smoke test; weak as evidence.
6. **Privacy tests have blind spots.** `tests/test_privacy.py` scans only
   `pipeline/` and `recommender/`; `app/` and `export/` are unscanned for
   telemetry, and the network check is string matching ("import requests"),
   not a runtime guarantee.
7. **A11y gate audits a proxy, not the product.** The axe gate runs on
   `app/render.py`'s static HTML; the actual Streamlit DOM that users interact
   with is never mechanically audited (only the deferred manual walkthrough
   covers it).
8. **OAuth hardening gaps.** The dashboard generates a `state` token
   (`app/dashboard.py:86`) but the paste-the-code flow never verifies the
   returned state; there is no PKCE; the client secret sits in env for what is
   effectively a native/local app.
9. **Small hygiene items.** `numpy>=1.26` is a declared runtime dependency but
   is imported nowhere in `pipeline/`, `recommender/`, `app/`, or `export/`
   (verified by grep). Uncommitted working-tree noise exists
   (`docs/audits/coverage.xml` modified, `eval-report.json` untracked) —
   generated artifacts churn against git. `pip-audit` reportedly errors on the
   editable local install (the Makefile works around it with
   `--skip-editable`); a nit, not a structural fix.
10. **Confidence numbers are hand-set.** `_SOURCE_BASE_CONFIDENCE` (0.95 /
    0.80 / 0.70 in `pipeline/identity.py`) is displayed to users as
    "confidence 80%" (`recommender/why.py::artist_identity_phrase`) — an
    ordinal editorial judgment presented with numeric precision it doesn't
    have.

## Strategic position in the portfolio

This repo is the **personal-track twin of queer-the-stacks**: both are
"recommendation with an explicit values lens" systems, and this one holds the
strongest version of the sourced-not-inferred identity pattern (type-level
invariants + AST guardrail test + boost-only rerank). Its portfolio value is
less the app itself (single-user, demo-mode) than the **reusable, provable
pattern** — which is currently trapped inside this repo rather than shared.
It is also the natural home for the portfolio's most publishable
engineering-ethics writeup (ROADMAP §9 calls the writeup "the artifact"; it
does not exist yet). The main credibility gap between "portfolio piece" and
"real tool" is the unwired live path (debt item 1) — every other gap is
secondary to that.
