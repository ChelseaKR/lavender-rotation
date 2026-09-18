# Definition of Done

CODEOWNERS-protected (see `CODEOWNERS`) — changing what "done" means here should get the same
review attention as the safety-core files. This is the checked-in version of the checklist that
also lives, informally, in `CLAUDE.md`'s "Definition of done" line and `CONTRIBUTING.md`'s PR
checklist; this file is the source of truth if the three ever drift.

A change (feature, fix, or refactor) is done when:

1. **`make verify` is green locally** — lint (`ruff format --check` + `ruff check`), `mypy --strict`,
   tests (≥85% branch-aware coverage on `pipeline`/`recommender`/`export`/`app`), security (`pip-audit` +
   secret scan), accessibility (`axe` = 0 violations on the rendered dashboard), the offline eval
   (hybrid beats the popularity baseline *and* does not regress vs `docs/audits/eval-baseline.json`
   by more than its stated tolerance), and the i18n `N/A`-declaration gate.
2. **The identity invariants still hold** wherever a read or ranking path was touched: identity is
   sourced-only and cited (never inferred), "unknown" is never reduced/down-ranked/dropped, and
   "female-fronted" stays distinct from any individual's gender. New tests assert these on any
   newly-touched surface.
3. **Every recommendation surface still shows why + identity basis + source** (the raw value each
   source asserted, not just a label) — `recommender/why.py` is the single source of truth for this
   wording; a change should not duplicate it elsewhere.
4. **Docs are updated to match**, including (as applicable): `CHANGELOG.md`'s `[Unreleased]`
   section, `docs/ROADMAP.md` if a decision or scope line changed, an ADR under `docs/adr/` if a
   new architectural or process decision was made, and the affected `docs/audits/*.md` artifact if
   its underlying commitment moved.
5. **Rollback is understood** — for anything touching the identity resolver, the re-rank, the
   cache schema, or CI/release configuration, the PR description states how to revert safely (the
   cache's forward-only schema migrations in particular are not reversible in place).
6. **No secrets, no scraped identity data** — `scripts/secret-scan.sh`/gitleaks pass, and nothing
   adds or redistributes a bulk musician-identity dataset (`docs/audits/identity-data-ethics.md`).

This file governs ordinary changes. Release-specific done-criteria (SBOM, signing, tag-triggered
re-verification) live in `.github/workflows/release.yml` and are exercised only at a `v*` tag push
— see `SECURITY.md`/`CHANGELOG.md` for the current (unreleased) release stance.
