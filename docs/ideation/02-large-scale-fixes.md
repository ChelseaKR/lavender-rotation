# Large-Scale Fixes (2026-07-01)

Last verified: 2026-07-12

## Current disposition

This was an ideation backlog, not a promise to ship every entry. After the PR
drain, the ledger is:

- **Implemented:** FIX-02, FIX-05 through FIX-08, plus FIX-10 through
  FIX-14. Their acceptance paths are covered by the cache/ingest, fairness,
  OAuth, property, operability, benchmark, and confidence tests and docs.
- **Partially implemented:** FIX-04's dedupe, TTL storage primitive, migrations,
  and before/after diffing are shipped. `wad refresh` currently replays demo
  fixtures and does **not** call an upstream identity enricher, so real
  re-enrichment/correction fold-back remains open and depends on FIX-01.
- **Deferred:** FIX-01 (live username-to-enriched-catalog orchestration) and
  FIX-03 (cross-provider canonical entity resolution). Shipping either without
  real provider contracts, credentials, and live-data validation would turn a
  demo into a falsely advertised production path. The README now states this
  boundary explicitly.
- **Human/external-gated:** FIX-09. The static product artifact passes three
  pa11y/axe renders, but Streamlit DOM behavior and a VoiceOver/NVDA walkthrough
  require an actual browser/assistive-technology pass. A re-platform is not
  justified without findings from that pass.

The original candidate descriptions follow for historical context.
Effort tiers: S (≤1 day), M (2–5 days), L (1–3 weeks), XL (3+ weeks).

Known nit, mentioned but **not** counted as a large-scale fix: `pip-audit`
currently errors on the editable local install (the Makefile already passes
`--skip-editable`); worth a small follow-up in `Makefile`/docs, nothing more.

---

## FIX-01 — Wire the live path end-to-end

**Pitch:** Make the README's definition of done true: a real Last.fm username
produces real recommendations.

- **Why it matters:** The dashboard always loads the demo world
  (`app/dashboard.py:135–137`) and `pipeline/cli.py` never constructs
  `LastfmClient`. Crucially, `pipeline/enrich.py` has **no live fetcher** —
  only parsers and `FixtureEnricher`. Until this lands, the repo's central
  promise is aspirational, which undercuts the credibility of everything else
  (for Chelsea as the primary user, and for anyone evaluating the portfolio).
- **Shape of work:** Add `MusicBrainzClient` / `WikidataClient` /
  `DiscogsClient` implementing `EnrichmentSource`, mirroring the
  `LastfmClient` pattern (rate-limited, HTTP-cached via
  `Cache.get_cached_response`, live calls `# pragma: no cover`, parsers
  already tested). Wire a `live_world(username)` factory next to
  `pipeline/demo.py`'s demo factories; branch `app/dashboard.py` and add
  `wad recommend --live`. Update `docs/audits/privacy-notes.md` (the "network
  confined to `lastfm.py`" claim must widen to the enrichment clients) and
  `tests/test_privacy.py` allowlist in the same PR.
- **Effort:** L.
- **Risks/deps:** API terms and rate limits (MusicBrainz 1 req/s, Discogs
  auth); user-agent requirements; interacts with FIX-03 (keying) and FIX-04
  (cache TTL) — sequence those first or together.
- **Excellent looks like:** `wad recommend --live --user <name>` works with
  only `WAD_LASTFM_API_KEY` (+ optional Discogs token); a second run makes
  zero network calls (cache hit rate reported); privacy tests updated in the
  same commit; no coverage drop on core logic.

## FIX-02 — Paginated, incremental scrobble ingest

**Pitch:** Ingest full listening histories, resumably, instead of one page of
200 recent tracks.

- **Why it matters:** Real scrobble histories run 10⁴–10⁵ plays.
  `LastfmClient.recent_scrobbles` makes one call; the recommender's ground
  truth ("your library leans toward women… by taste", README) is meaningless
  on a 200-play sample. Affects recommendation quality and eval validity.
- **Shape of work:** Pagination loop over `user.getrecenttracks` with
  `from=<last synced ts>`; store a per-user sync watermark in the cache; make
  `ingest()` (`pipeline/ingest.py`) incremental (merge, don't rebuild).
  Depends on FIX-04's dedupe.
- **Effort:** M.
- **Risks/deps:** Long first sync (needs progress output — FIX-12); rate
  limiting already exists (`RateLimiter`).
- **Excellent looks like:** Full-history first sync completes unattended
  within Last.fm's limits; subsequent syncs fetch only deltas; play counts
  stable across repeated runs (regression-tested with a fixture that
  paginates).

## FIX-03 — Canonical artist entity resolution

**Pitch:** One artist, one key, across Last.fm, MusicBrainz, Wikidata, and
Discogs.

- **Why it matters:** `parse_recent_tracks` (`pipeline/lastfm.py:164`) keys on
  `mbid or artist-name`; similarity results key the same way. Name-keyed and
  MBID-keyed records for the same act silently split, which corrupts play
  weights, dedupe, enrichment joins, and eval ground truth
  (`recommender/eval.py::ground_truth` has the same `id or name` fallback).
  Correctness debt that grows with FIX-01/FIX-02.
- **Shape of work:** An `ArtistKey` resolution layer in `pipeline/`: MBID
  first; explicit `name:<normalized>` fallback namespace; an alias table in
  the cache (`pipeline/cache.py` schema addition) recording merges with
  provenance; a backfill pass that upgrades name-keys to MBIDs when
  enrichment discovers them. Never merge on fuzzy heuristics without a
  recorded source — same sourced-not-inferred discipline, applied to identity
  *of the record* rather than of the person.
- **Effort:** L.
- **Risks/deps:** Cache migration (FIX-04's schema versioning); subtle eval
  churn (snapshot tests in `tests/test_reproducibility.py` will need
  intentional updates).
- **Excellent looks like:** A property test proving no artist appears under
  two keys after ingest+enrich; merge decisions queryable with citations;
  zero silent merges.

## FIX-04 — Cache lifecycle: dedupe, TTL, re-enrichment, migrations

**Status:** Partial. Storage lifecycle and deterministic diffing are implemented;
the CLI is explicitly demo-only until a live enricher exists.

**Pitch:** Turn the cache from an append-only scratchpad into a managed local
datastore.

- **Why it matters:** `Cache.put_scrobbles` (`pipeline/cache.py:101`) inserts
  without a uniqueness constraint (duplicate rows on re-ingest); `http_cache`
  never expires (stale identity claims persist forever, contradicting RR-2's
  correction story); there is no schema version, so any schema change breaks
  existing caches silently.
- **Shape of work:** `UNIQUE(username, artist_id, track, ts)` on scrobbles
  (INSERT OR IGNORE); per-table TTL policy (identity sources re-checked on a
  cadence matching `identity-data-ethics.md`'s "recheck per identity-source
  API change"); a `wad refresh [--artist X]` command that forces re-enrichment
  and reports label changes (the correction mechanism ROADMAP §9 promises);
  `PRAGMA user_version` + tiny migration runner.
- **Effort:** M.
- **Risks/deps:** None hard; unblocks FIX-01/02/03.
- **Excellent looks like:** Re-running ingest twice is byte-identical in the
  DB; `wad refresh` prints a diff of changed identity labels with old/new
  citations; opening an old-version cache either migrates or fails with a
  clear message — never silently misreads.

## FIX-05 — Computed exposure & rank-fairness metrics

**Pitch:** Turn the fairness narrative into generated numbers in the committed
eval artifact.

- **Why it matters:** The project's headline claim is fairness-shaped, but
  `recommender/eval.py` computes only precision/recall/MAP; nothing measures
  exposure share by identity basis (woman / nonbinary / female-fronted /
  man / unknown), unknown-retention@k as lens strength varies, or mean rank
  shift under the lens. `docs/audits/fairness-identity.md` currently rests on
  unit tests plus prose. For a portfolio whose ethos is "never fake, always
  measure", this is the highest-leverage credibility fix after FIX-01.
- **Shape of work:** An `exposure_report(recs_by_lens: dict[float, list[Recommendation]])`
  module in `recommender/` computing: exposure@k per identity segment,
  unknown-retention curve across lens strengths 0→1, rank-shift distribution,
  and a popularity-tier × identity cross-tab (using `Artist.listeners`, which
  exists for exactly this — `pipeline/models.py:228`). Emit into
  `docs/audits/eval-report.json` via `pipeline/cli.py eval`; add a
  merge-blocking assertion that unknown-retention is 100% at every lens
  strength (the mechanical form of the fairness guarantee, verified on output
  rather than only on the rerank function).
- **Effort:** M.
- **Risks/deps:** Metric choice needs a short written justification (exposure
  vs. attention-weighted exposure); pairs with EXP-01 (surfacing it in the UI).
- **Excellent looks like:** `eval-report.json` shows per-segment exposure at
  lens 0.0/0.5/1.0; CI fails if an unknown artist ever loses rank to the lens;
  `fairness-identity.md` cites computed numbers, not just tests.

## FIX-06 — De-circularize the eval

**Pitch:** Stop grading the recommender on the fixture that was tuned to make
it pass.

- **Why it matters:** `pipeline/demo.py`'s docstring admits the demo world is
  "tuned so the hybrid recommender recovers held-out discoveries". The CI eval
  gate (`make eval`) therefore proves tautology, not quality. Fine as a smoke
  test; misleading as the repo's only quantitative evidence.
- **Shape of work:** (a) A fixture-family generator: several synthetic worlds
  with varied shapes (sparse tags, popularity-skewed, no-collaborative-signal,
  adversarial near-misses), eval run across all, results aggregated;
  (b) a clearly separated `make eval-real` harness that runs against the
  operator's own cached scrobbles **locally only** — never in CI, results
  summarized (not raw data) if committed; (c) report effect sizes, not just a
  boolean `hybrid_beats_popularity` (`recommender/eval.py::to_report`).
- **Effort:** M.
- **Risks/deps:** Real-data leg is human-gated (see 04 §gated items); depends
  on FIX-01/02 for real ingestion.
- **Excellent looks like:** CI eval covers ≥4 structurally different fixture
  worlds; the demo-world tuning caveat is documented in the report itself;
  a real-data eval has been run once locally and its summary committed with a
  date and an honest n.

## FIX-07 — Runtime egress guard across all packages

**Pitch:** Upgrade the privacy guarantee from "grep says so" to "the socket
layer enforces it".

- **Why it matters:** `tests/test_privacy.py` scans only `pipeline/` and
  `recommender/` source text. `app/` and `export/` are not scanned for
  telemetry SDKs at all, and string matching misses indirect egress (a
  transitive import, an `httpx` adoption, `webbrowser` calls). The privacy
  posture is the portfolio's brand; its enforcement should be structural.
- **Shape of work:** (a) Extend the source scan to `app/` and `export/` with
  an explicit allowlist (`pipeline/lastfm.py`, `export/spotify.py`
  `RequestsTransport` only); (b) add an autouse pytest fixture that patches
  `socket.socket.connect` to raise on any test-time network attempt, proving
  the entire suite is offline by construction; (c) document the allowlist in
  `docs/audits/privacy-notes.md` as the single egress registry that FIX-01's
  new clients must join.
- **Effort:** S–M.
- **Risks/deps:** Must land before/with FIX-01 (which widens the allowlist).
- **Excellent looks like:** A deliberately-added `requests.get` anywhere in
  `app/` fails two independent gates (scan + socket guard) before review.

## FIX-08 — OAuth hardening: PKCE, loopback listener, state verification

**Pitch:** Make the Spotify flow follow current native-app OAuth best practice.

- **Why it matters:** `app/dashboard.py` generates a CSRF `state` (line 86)
  but the paste-the-code flow never verifies the state Spotify returns — the
  protection is decorative. There is no PKCE, and a long-lived client secret
  lives in env for a local app. Security review-gate credibility
  (`docs/audits/residual-risk.md` threat model) depends on this being right.
- **Shape of work:** In `export/spotify.py`: PKCE (S256 challenge) on
  `SpotifyOAuth`; a tiny stdlib loopback HTTP listener (127.0.0.1 redirect)
  that captures `code` **and** `state` and verifies the state match; keep the
  paste flow as fallback but require the full redirected URL (so state can be
  checked) rather than the bare code. All offline-testable through the
  existing `HttpTransport` fake.
- **Effort:** M.
- **Risks/deps:** Spotify app settings (redirect URI); none internal.
- **Excellent looks like:** State mismatch is a tested failure path; PKCE
  verifier never leaves memory; threat-model table row updated; the flow works
  without the user copy-pasting URL fragments by hand.

## FIX-09 — A11y parity for the interactive surface

**Pitch:** Audit the DOM users actually touch, not only the static proxy.

- **Why it matters:** The axe gate runs on `app/render.py`'s hand-crafted HTML
  (excellent), but Streamlit generates its own widget DOM for the real
  dashboard, which is never mechanically checked. The gap is acknowledged only
  implicitly (coverage omit for `app/*`, manual walkthrough pending). WCAG 2.2
  AA is a release gate; the gate should see the release surface.
- **Shape of work:** Two options, decide by ADR:
  (a) an opt-in `make a11y-live` target that launches Streamlit headless and
  runs axe via Playwright against it (not merge-blocking initially — Streamlit
  DOM is third-party; findings triaged into "ours" vs "upstream"); or
  (b) the larger move: replace Streamlit with a thin FastAPI/htmx (or static
  + tiny JS) UI built directly on `render.py`, making the audited artifact
  *be* the product (ROADMAP §6 ADR already notes React as a later option —
  this is the accessibility-driven version of that decision).
- **Effort:** M (option a) / XL (option b).
- **Risks/deps:** Option b touches everything user-facing; do option a first
  and let its findings justify or kill option b.
- **Excellent looks like:** A dated report of axe findings on the *running*
  app in `docs/audits/`; upstream Streamlit issues filed or worked around;
  the manual SR walkthrough checklist executed against the same surface.

## FIX-10 — Source-conflict surfacing and a local correction ledger

**Pitch:** When sources disagree about someone's identity, show the
disagreement; when a source is wrong, record the correction locally with a
citation.

- **Why it matters:** `pipeline/identity.py::resolve_identity` silently picks
  the highest-priority source on conflict and caps confidence at 0.5 — the
  conflict itself never reaches the user, though the why-card shows all
  citations. And the promised correctability (RR-2,
  `identity-data-ethics.md` "Correctability") has no mechanism beyond "fix it
  upstream and re-enrich". Misgendering-by-stale-source is the project's #1
  residual harm; this is its direct mitigation.
- **Shape of work:** (a) Add `conflict: bool` (or the disagreeing values) to
  `IdentityLabel`/`WhyThisArtist` and render it honestly ("sources disagree:
  Wikidata asserted X on 2026-05-31; MusicBrainz asserted Y…") in
  `recommender/why.py` and `app/render.py`; (b) a local `corrections` cache
  table: an operator-entered override **that itself requires a citation**
  (an `ARTIST_STATEMENT`-kind source — no citation, no override, preserving
  the no-inference invariant), applied at resolve time, listed by
  `wad corrections`, and surfaced in provenance as "local correction".
- **Effort:** M.
- **Risks/deps:** Serde + cache schema change (FIX-04); wording review is a
  review-gate (framing of disagreement must be respectful).
- **Excellent looks like:** A conflicted label is visually distinct with both
  claims shown; an uncited override is unconstructible (model invariant, with
  a test in the spirit of `tests/test_identity_model.py`); corrections survive
  `wad refresh`.

## FIX-11 — Property-based guardrail testing

**Pitch:** Prove the invariants over generated inputs, not just hand-picked
examples.

- **Why it matters:** The guardrails are the product. Current tests are strong
  but example-based; Hypothesis can search the input space for the exact edge
  the team didn't think of (e.g. serde round-trips of exotic-but-legal labels,
  rerank monotonicity, resolver behavior on arbitrary evidence multisets).
- **Shape of work:** Add `hypothesis` to the dev extra
  (`pyproject.toml`); strategies for `Source`/`IdentityEvidence`/`Artist`;
  properties: (1) `resolve_identity` never returns non-unknown without ≥1
  individual source; (2) `rerank` never lowers any score and is a permutation;
  (3) `artist_from_dict(artist_to_dict(a)) == a`; (4) corrupt-dict loading
  either round-trips or raises `IdentityError` — never yields an unsourced
  label; (5) `sort_and_rank` determinism.
- **Effort:** S.
- **Risks/deps:** None; pure addition. Keep examples DB out of git.
- **Excellent looks like:** Five properties running in CI within seconds;
  at least one previously-unknown edge documented (or an explicit note that
  none was found).

## FIX-12 — Operability pass: logging, doctor, data location

**Pitch:** Make failure states legible for a tool that talks to four external
APIs.

- **Why it matters:** There is no logging anywhere in `pipeline/` or
  `export/`; a live-mode failure (rate limit, expired token, malformed
  payload) surfaces as a raw exception. `DEFAULT_DB_PATH` is a hardcoded
  relative `data/cache.db` (`pipeline/cache.py:22`) — running `wad` from
  another directory silently creates a second cache.
- **Shape of work:** stdlib `logging` with a local-only structured formatter
  (never network — reaffirmed by FIX-07's guard); `WAD_DATA_DIR` env +
  platformdirs-style default; `wad doctor` (checks keys present, cache
  readable/version, upstream reachability opt-in); timing summaries on ingest.
- **Effort:** S–M.
- **Risks/deps:** None; improves every other fix's debuggability.
- **Excellent looks like:** A failed live run tells the user which stage,
  which API, and what to do next; two shells in different cwd's share one
  cache.

## FIX-13 — Scale the scoring path (and settle the numpy question) — ✅ Done (2026-07-03)

**Pitch:** Make recommendation latency sane on full-history profiles — and
either use or drop the declared numpy dependency.

- **Why it matters:** `recommender/content.py` and `collaborative.py` are
  pure-dict Python loops — fine for 14 demo artists, unproven for the
  thousands of candidates a full history (FIX-02) produces. Meanwhile
  `numpy>=1.26` is a runtime dependency in `pyproject.toml` that is imported
  **nowhere** (grep-verified) — dead supply-chain surface that pip-audit and
  reviewers still pay for.
- **Shape of work:** Benchmark first (a `make bench` with a generated 5k-artist
  world); if needed, vectorize the tag-cosine with numpy (justifying the dep)
  or drop numpy from `[project.dependencies]`. Candidate-set pruning
  (top-N per seed) in `collaborative_scores`. Record the decision as an ADR
  bullet in ROADMAP's build log per the Documentation Standard.
- **Effort:** M.
- **Risks/deps:** After FIX-02 (real scale exists only then); reproducibility
  snapshots must remain byte-stable (`tests/test_reproducibility.py`).
- **Excellent looks like:** p95 end-to-end recommend < 2 s on a 50k-scrobble /
  5k-candidate profile, measured and committed; zero unused runtime deps.
- **Status:** Implemented. The numpy question is settled: `numpy>=1.26` is
  dropped from `[project.dependencies]` in `pyproject.toml` (it was imported
  nowhere — `recommender/content.py`'s tag cosine is a sparse dict dot
  product, which does not benefit from dense numpy vectors) and `uv.lock`
  regenerated. `scripts/bench.py` (`make bench`) builds a seeded, reproducible
  synthetic world (200 known + 5,000 candidate artists, 50,000 scrobbles) via
  `pipeline.models`/`pipeline.lastfm.FixtureLastfm`, and reports p50/p95 for
  `collaborative_scores`, `content_scores`, and the end-to-end `recommend()`
  over 20 iterations — all randomness is confined to world generation, never
  the scored path, so `tests/test_reproducibility.py` stays byte-stable
  (verified). Measured p95 for `recommend()` is ~140 ms on this machine,
  comfortably under the 2 s target — `content_scores` (~60 ms), not
  `collaborative_scores` (~10 ms), is the larger of the two signal costs, but
  neither is a hotspot at this scale, so no candidate-set pruning was added
  (kept the diff minimal per the "only if needed" gate in the shape of work).

## FIX-14 — Honest confidence semantics

**Pitch:** Stop presenting hand-set constants as percentages.

- **Why it matters:** `_SOURCE_BASE_CONFIDENCE` (0.95/0.80/0.70,
  `pipeline/identity.py`) is an editorial ordering, but
  `artist_identity_phrase` renders "(confidence 80%)" — numeric precision the
  system does not possess. For a project whose thesis is epistemic honesty
  about identity, this is a small but real inconsistency.
- **Shape of work:** Replace the displayed number with qualitative tiers tied
  to provenance ("directly stated by the artist" / "recorded in Wikidata" /
  "editorial database entry"; "sources disagree" per FIX-10), keeping the
  numeric field internal for ordering only — or remove it from
  `IdentityLabel` entirely and derive ordering from `_SOURCE_PRIORITY`.
  Update `recommender/why.py`, `app/render.py`, and the wording tests.
- **Effort:** S.
- **Risks/deps:** Pairs naturally with FIX-10; wording is review-gated.
- **Excellent looks like:** No unexplained numbers in any identity statement;
  the tier vocabulary documented in `docs/audits/identity-data-ethics.md`.
