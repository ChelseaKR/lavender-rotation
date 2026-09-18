# Documentation Audit

Last verified: 2026-07-19 · Recheck cadence: per docs change. Base branch: `main`.

This audit records the documentation sweep and remediation loop for this repository. It checks the docs as a system: entry points, root-level process and legal files, project scope, setup and validation notes, safety and privacy posture, architecture and planning docs, local links, and the places where code, tests, workflows, and docs meet.

## Audit Results

| Area | Result | Evidence |
| --- | --- | --- |
| Entry docs | pass | `README.md` present |
| Security/process docs | pass | CONTRIBUTING.md, SECURITY.md, CHANGELOG.md |
| Architecture/planning docs | pass | every ADR under `docs/adr/` (13 at the time of writing; `ls docs/adr/*.md | wc -l` is the live count), roadmap, ideation notes, and the research docs |
| Safety/privacy/audit docs | pass | every doc under `docs/audits/` plus this file and `docs/RESPONSIBLE-TECH-AUDITS.md` |
| Validation surface | pass | every Python file under `tests/` and every file under `.github/workflows/` (recompute with `git ls-files`; the transcribed counts here went stale within a few merges and nothing noticed) |
| Local doc links | pass | Authored-doc relative links rechecked; 0 unresolved |

## Root-Level Documentation Audit

This section covers hand-authored documentation at the repository root and root-adjacent GitHub templates. It is separate from the `docs/` inventory so README, process, legal, release, and project-specific root files do not get hidden inside the larger docs tree.

| Surface | Result | Evidence |
| --- | --- | --- |
| Root README | pass | Present: `README.md` |
| Root process docs | pass | Present: `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md` |
| Root legal, citation, and conduct docs | pass | Present: `LICENSE`, `NOTICE`, `CITATION.cff`, `CODE_OF_CONDUCT.md` |
| Other root project docs | info | `DEFINITION_OF_DONE.md` |
| Root-adjacent GitHub templates | pass | `.github/PULL_REQUEST_TEMPLATE.md` |
| Root/template doc links | pass | 15 root-level/template links checked; 0 unresolved |

Root-level files checked:

- `CHANGELOG.md`
- `CITATION.cff`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `DEFINITION_OF_DONE.md`
- `LICENSE`
- `NOTICE`
- `README.md`
- `SECURITY.md`

Root-adjacent template files checked:

- `.github/PULL_REQUEST_TEMPLATE.md`

## Remediation In This PR

- Added the missing root `NOTICE` identified by the audit loop.
- Added `docs/PROJECT-SCOPE.md` as the plain-language project and boundary map.
- Added this audit record so future doc changes have a dated baseline.
- Added or refreshed the docs index so scope, audit, and primary docs are easy to find.
- Fixed or added root/doc remediation files: `NOTICE`.

## Repo Surfaces Checked

Package and workspace metadata:

- Python package `lavender-rotation` (>=3.12, ADR 0004).

Source and operations surfaces seen at the repo root:

- `app/`
- `export/`
- `Makefile`
- `pipeline/`
- `pyproject.toml`
- `scripts/`
- `recommender/`
- `tests/`
- `uv.lock`

Workflow files checked:

- `.github/workflows/ci.yml`
- `.github/workflows/codeql.yml`
- `.github/workflows/mutation.yml`
- `.github/workflows/osv-scanner.yml`
- `.github/workflows/release.yml`
- `.github/workflows/scorecard.yml`
- `.github/workflows/trufflehog.yml`
- `.github/workflows/zizmor.yml`

## Documentation Inventory

| Category | Count | Representative files |
| --- | ---: | --- |
| architecture and interfaces | every ADR | `docs/adr/0000-record-architecture-decisions.md`, `docs/adr/0004-python-floor-3.12.md`, `docs/adr/0007-sourced-only-identity-unknown-first-class.md`, `docs/adr/0010-release-producing-repository.md`, plus the rest under `docs/adr/` |
| entry points and repo process | 9 | `.github/PULL_REQUEST_TEMPLATE.md`, `CHANGELOG.md`, `CITATION.cff`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `LICENSE`, `NOTICE`, `README.md`, plus 1 more |
| other docs | 5 | `CODEOWNERS`, `DEFINITION_OF_DONE.md`, `docs/I18N.md`, `docs/PROJECT-SCOPE.md`, `docs/README.md` |
| planning and research | 8 | `docs/ROADMAP.md`, `docs/RESEARCH-ROADMAP.md`, `docs/USER-RESEARCH.md`, and `docs/ideation/` |
| safety, privacy, accessibility, and audits | all | `docs/DOCUMENTATION-AUDIT.md`, `docs/RESPONSIBLE-TECH-AUDITS.md`, both accessibility reports, and the audit cards/registers under `docs/audits/` |
| methods writeup | 1 | `docs/writeup/methods.md` |

Full hand-authored doc inventory checked by this pass:

- `.github/PULL_REQUEST_TEMPLATE.md`
- `CHANGELOG.md`
- `CITATION.cff`
- `CODEOWNERS`
- `CODE_OF_CONDUCT.md`
- `CONTRIBUTING.md`
- `DEFINITION_OF_DONE.md`
- `LICENSE`
- `NOTICE`
- `README.md`
- `SECURITY.md`
- `docs/DOCUMENTATION-AUDIT.md`
- `docs/I18N.md`
- `docs/PROJECT-SCOPE.md`
- `docs/README.md`
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
- `docs/ideation/01-deep-dive.md`
- `docs/ideation/02-large-scale-fixes.md`
- `docs/ideation/03-expansions.md`
- `docs/ideation/04-impact-and-sequencing.md`
- `docs/ideation/README.md`
- `docs/writeup/methods.md`

## Link Check

- Checked local links in authored Markdown and MDX docs.
- Unresolved authored-doc links after remediation: 0.
- Root-level/template unresolved links after remediation: 0.

## Validation Notes

- The audit was generated from a clean worktree based on `origin/main` for this PR branch.
- Ran a local relative-link check over hand-authored Markdown and MDX docs.
- Ran an explicit root-level documentation presence and link check for README, process, legal, project, and template docs.
- Ran `git diff --check` across the PR worktrees after remediation.
- Product test suites remain the authority for runtime behavior; this PR changes documentation only.
