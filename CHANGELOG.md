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

### Added
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
