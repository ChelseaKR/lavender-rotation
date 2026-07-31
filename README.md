# Women-Artist Discovery

**A demo-first music-discovery engine that surfaces new women, nonbinary, and female-fronted artists through an explicit values lens.** It combines collaborative and content signals with a sourced-identity re-ranker. Identity is never inferred, and "unknown" is a normal, first-class answer.

**Trans women are women here — explicitly.** The three terms in the tagline are not redundant; they cover three different shapes: *women* (solo artists whose sourced self-identification is woman — cis or trans, with no distinction drawn anywhere in the data model), *nonbinary* artists (represented as nonbinary, never folded into another category), and *female-fronted* (band-composition metadata: an act whose sourced lineup/role data shows a woman — cis or trans — fronting it, which is a fact about the band, never a claim about any individual). A trans woman artist whose self-identification is sourced is surfaced as a woman, full stop.

**Status:** `Beta` · **Track:** Personal (data/ML + small web app) · **License:** MIT · **Data:** personal/local

## Quickstart

```sh
make install && make dev   # offline demo mode — no API key needed
make verify                # run the full merge gate locally
```

Demo mode ships a clearly-labeled synthetic world (`pipeline/demo.py`), so you can
explore recommendations, the fairness/exposure panel, and per-pick explanations
without an account anywhere. Wiring a real Last.fm username through live
enrichment is the next milestone (see [Project status](#project-status)).

## Why it matters
Your library leans toward women and female-fronted bands by taste, but no recommender helps you lean into that on purpose without either ignoring identity entirely or guessing it crudely. Doing this *well* — sourced, transparent, non-essentialist — is the whole point and the interesting part.

## What it does
- **Builds listening profiles** from Last.fm-shaped scrobbles and tags; paginated/incremental client and cache paths are tested, while live app orchestration is still deferred.
- **Hybrid recommendations:** collaborative similarity + content/tags, then a values-aware re-rank.
- **Sourced identity, never inferred:** identity basis is shown and cited; woman means woman, cis or trans, with no distinction drawn; nonbinary is represented properly; unknown artists are surfaced on musical merit alone.
- **Explains every pick:** a shared "Why this artist" view — why (which signals) + identity basis + provenance (the *raw value each source asserted*, never inferred).
- **Export your picks:** push the current set to a Spotify playlist (env-configured OAuth), or download a portable, account-free track list (plain text / CSV / M3U / JSPF) that imports into most players and transfer tools. More native platforms (Apple Music, Tidal, Qobuz) are planned — [#54](https://github.com/ChelseaKR/women-artist-discovery/issues/54).
- **Local-first:** your listening history stays yours. Sanctioned egress is limited to explicit Last.fm fetches, opt-in upstream diagnostics, and user-initiated playlist export (artist/track names only).

## Guardrails

These are hard rules, each enforced by a merge-blocking test (see
`tests/test_no_inference.py`, the centrepiece):

- **Never infer an artist's gender or identity** from name, voice, image, genre, or any heuristic — identity labels come only from cited self-identification sources (artist statement, sourced Wikidata P21 claim, MusicBrainz gender field) and must carry that citation.
- **Woman includes trans women explicitly** — sourced self-identification is the only test, and no cis/trans distinction exists anywhere in the vocabulary.
- **"Unknown" is first-class** and must never reduce, down-rank, or drop a recommendation; the values lens only ever boosts.
- **"Female-fronted" is band-composition metadata** (lineup/role), sourced not guessed, and kept distinct from any individual's gender.
- **Every recommendation shows its work:** why + identity basis + source.
- **No redistribution of a scraped musician-identity dataset** — minimize, cite, keep correctable.

## Project status

The offline demo and full pipeline are implemented and gated: `make verify` runs
formatting/lint/SAST, strict typing, 433 tests at 97% coverage, dependency and
secret scans, axe/pa11y renders plus browser-driven keyboard/reflow/reduced-motion
specs (Playwright, required in CI), offline multiworld evaluation with
regression/fairness gates, and the i18n declaration gate. CodeQL, zizmor, OSV,
Scorecard, release, and CI workflows all run hosted. Still open: live
username-to-recommendation orchestration (deferred in the roadmap ledger), and
review-gated manual screen-reader/keyboard sign-offs — see
[`docs/audits/`](./docs/audits/).

`wad refresh` is deliberately labeled **demo-only**: it exercises cache expiry and
before/after reporting with the committed fixture catalog, but it does not query an
upstream identity provider. Real correction fold-back remains open with the deferred
live-enrichment work; the command prints this limitation on every run.

This project is built in the open: [`docs/RESEARCH-ROADMAP.md`](./docs/RESEARCH-ROADMAP.md),
[`docs/ideation/`](./docs/ideation/), and [`docs/USER-RESEARCH.md`](./docs/USER-RESEARCH.md)
are working notes, published deliberately and labeled for what they are (the
user-research personas are synthetic, and say so at the top).

## Observability
**Tier C** — OTel tracing is out of scope for this local tool. The CLI configures
structured stage/timing logs, supports `--log-format json`, and `wad doctor`
reports local configuration/cache health with opt-in upstream probes.

## AI-evaluation status
In scope per `AI-EVALUATION-STANDARD.md` §0 as a classical-ML recommender: no LLM,
RAG, generation, or judge. Direct runtime dependency is `requests`; Streamlit/pandas
live in the app extra and test/audit tools live in the dev group. §1–3's
LLM/RAG/judge gates are dormant. The gate that **is** active and merge-blocking:
the offline eval must beat the popularity baseline (`make eval`, `docs/audits/eval-report.json`).
The first LLM SDK import anywhere in this repo flips this status to `APPLIES` in full and activates
§1–3. See `docs/RESPONSIBLE-TECH-AUDITS.md` for the full AI-governance picture.

## Standards Conformance
Inherits [`/STANDARDS`](../STANDARDS/). Per-standard declarations (Documentation Standard's
"a repo must declare Applies/N/A for every standard, not just inherit silently" rule):

| # | Standard | Status | Notes |
|---|----------|--------|-------|
| 1 | Quality & Metrics | Applies | ROADMAP §7 metrics ledger; `make verify` enforces the current gates. |
| 2 | Code Quality | Applies | Ruff, strict mypy, per-module coverage floor, PEP 735 dev group, and complexity checks are active. Mutation testing (CQ-47) gates the safety-critical modules — `make mutation` fails under 70% mutants killed; weekly + on-demand in CI (`mutation.yml`). |
| 3 | Security & Supply-Chain | Applies — **ASVS 5.0 Level 1** | No auth / no multi-user surface, so L2 controls are N/A (no server); see `docs/RESPONSIBLE-TECH-AUDITS.md` §F |
| 4 | CI/CD | Applies | CODEOWNERS, workflows, and the live main ruleset are configured; hosted execution restored 2026-07-19 (repo made public — free runner minutes). |
| 5 | Release & Versioning | Applies — **release-producing, unreleased** | No tag/release exists yet; see `CHANGELOG.md` and `SECURITY.md` for the current stance |
| 6 | Accessibility | Applies | axe gate blocking (0 violations) + Playwright keyboard/reflow/reduced-motion specs (`tests/test_e2e_a11y.py`); Lighthouse not wired; manual screen-reader + keyboard sign-offs pending the first release (`docs/audits/accessibility-2026-07-17.md`) |
| 7 | Observability | Applies — **Tier C** | See Observability section above |
| 8 | Internationalization | **N/A — single-user operator-only output** | Scope decision in `docs/I18N.md`, self-enforced via `scripts/i18n-gate.sh` |
| 9 | AI Evaluation | Applies — **narrow** | See AI-evaluation status above |
| 10 | Documentation | Applies | ADR log, documentation audit, citation validation, and staleness gate are active. |
| 11 | Responsible-Tech Framework | Applies | Audits A–F committed — `docs/RESPONSIBLE-TECH-AUDITS.md` |

Open or human-gated gaps are dispositioned in `docs/RESEARCH-ROADMAP.md` and
`docs/ideation/`; they are not represented as shipped features.
