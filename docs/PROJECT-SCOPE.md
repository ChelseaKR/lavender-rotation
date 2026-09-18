# Project Scope

Last verified: 2026-07-19 · Recheck cadence: per docs change. Base branch: `main`.

This file is a plain-language map of the project as it exists on `main`. It does not replace the README, roadmap, audit docs, or source comments. It points to them so a reviewer can see the whole shape without reading every file first.

## What This Project Is

Lavender Rotation recommends music with an explicit values lens. It uses listening history and music metadata while keeping identity sourced, never guessed, and treating unknown identity as a valid answer.

Package metadata checked in this pass:

- Python package `lavender-rotation` for Python `>=3.12` (ADR 0004).

## Who It Serves

- A listener who wants music recommendations weighted toward women, nonbinary, and female-fronted artists.
- Maintainers building local-first recommender pipelines.
- Reviewers checking fairness, identity-data ethics, privacy, and export behavior.

## What It Covers

- Last.fm ingest, metadata enrichment, identity modeling, recommender logic, explanations, reranking, temporal profiles, per-artist feedback, exports, and dashboard rendering.
- Docs for roadmap, audits, ADRs, I18N, identity ethics, fairness, privacy, and residual risk.
- Eval reports, model cards, data cards, accessibility outputs, and AI risk notes.
- Tests for identity, privacy, adapters, reproducibility, export, eval, feedback, cache lifecycle, fairness observability, and accessibility.
- Spotify, TIDAL, and portable playlist export paths, behind one `Exporter` seam.

## How It Is Put Together

- pipeline/ handles ingest, enrichment, identity, models, and cache behavior.
- recommender/ contains collaborative, content, hybrid, rerank, explanation, and eval code.
- export/ contains the provider-agnostic `Exporter` seam (`base.py`), the Spotify and TIDAL adapters, and the credential-free tracklist outputs.
- app/ contains dashboard and static rendering.
- docs/audits/ records identity, fairness, model, data, privacy, and residual-risk notes.

Observed source and operations surfaces:

- `Makefile`
- `app/`
- `export/`
- `pipeline/`
- `pyproject.toml`
- `recommender/`
- `scripts/`

GitHub workflow files checked:

- `.github/workflows/ci.yml`
- `.github/workflows/codeql.yml`
- `.github/workflows/mutation.yml`
- `.github/workflows/osv-scanner.yml`
- `.github/workflows/release.yml`
- `.github/workflows/scorecard.yml`
- `.github/workflows/trufflehog.yml`
- `.github/workflows/zizmor.yml`

## Trust Boundaries

- Identity is sourced from cited self-identification or structured sources, never inferred from names, voices, images, or genre.
- Unknown identity must not lower the quality of a recommendation.
- The user controls egress; playlist export sends only what that export requires.

## Outside This Scope

- It is a personal recommender, not a universal fairness benchmark.
- Metadata sources can be wrong or incomplete.
- A scraped musician-identity dataset should not be redistributed from this repo.

## Docs And Evidence Checked

This pass checked every authored Markdown file, every Python file under `tests/`, and every
workflow file on `main`, excluding vendored provider licenses, dependency folders, generated cache
files, and generated HTML/JSON artifacts.

The counts used to be written out here as "34 authored Markdown files, 38 Python files under
`tests/`, and 7 workflow files". They were right on the day the pass ran and wrong within a few
merges (45 / 51 / 8 as of 2026-08-28), with nothing to notice — the same hand-typed-and-stale
failure `scripts/docs_figures.py` exists to prevent for the README's test count. A number
nobody regenerates is worse than no number, so recompute rather than read:

```sh
git ls-files '*.md' | wc -l
git ls-files 'tests/*.py' | wc -l
git ls-files '.github/workflows/*' | wc -l
```

Primary docs checked:

- `.github/PULL_REQUEST_TEMPLATE.md`
- `CHANGELOG.md`
- `CITATION.cff`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `DEFINITION_OF_DONE.md`
- `LICENSE`
- `README.md`
- `SECURITY.md`
- `docs/I18N.md`
- `docs/RESPONSIBLE-TECH-AUDITS.md`
- `docs/RESEARCH-ROADMAP.md`
- `docs/USER-RESEARCH.md`
- `docs/ROADMAP.md`
- `docs/adr/0000-record-architecture-decisions.md`
- `docs/adr/0001-single-maintainer-review-posture.md`
- `docs/adr/0002-python-floor-3.10-not-3.12.md`
- `docs/adr/0003-hatchling-build-backend.md`
- `docs/adr/0004-python-floor-3.12.md`
- `docs/adr/0005-streamlit-over-react.md`
- `docs/adr/0006-hybrid-recommender-over-single-method.md`
- `docs/adr/0007-sourced-only-identity-unknown-first-class.md`
- `docs/adr/0008-cache-and-rate-limit-respect.md`
- `docs/adr/0009-flat-package-layout-not-src.md`
- `docs/adr/0010-release-producing-repository.md`
- `docs/audits/accessibility-2026-05-31.md`
- `docs/audits/accessibility-2026-07-09.md`
- `docs/audits/ai-risk-register.md`
- `docs/audits/data-card.md`
- `docs/audits/fairness-identity.md`
- `docs/audits/identity-data-ethics.md`
- `docs/audits/model-card.md`
- `docs/audits/privacy-notes.md`
- `docs/audits/residual-risk.md`
- `docs/ideation/README.md`
- `docs/writeup/methods.md`

Representative test files checked (the full suite is authoritative):

- `tests/__init__.py`
- `tests/conftest.py`
- `tests/test_a11y.py`
- `tests/test_adapters.py`
- `tests/test_cache_serde.py`
- `tests/test_eval.py`
- `tests/test_explanation.py`
- `tests/test_export.py`
- `tests/test_identity_model.py`
- `tests/test_ingest.py`
- `tests/test_models.py`
- `tests/test_no_inference.py`
- `tests/test_privacy.py`
- `tests/test_provenance.py`
- `tests/test_reproducibility.py`
- `tests/test_rerank.py`
- `tests/test_unknown_first_class.py`
- `tests/test_why.py`

## Validation Notes

For this docs PR, validation means the scope file was reviewed against the merged `main` metadata and docs inventory, checked with `git diff --check`, and passed through the repository verification suite. Project tests remain the authority for code behavior.
