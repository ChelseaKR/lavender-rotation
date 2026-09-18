<!--
Thanks for the PR. See CONTRIBUTING.md for the full gate set and
DEFINITION_OF_DONE.md for what "done" means here. Delete any section that
truly doesn't apply, but don't skip the identity-invariant checklist if a
read/ranking path was touched — that's the one non-negotiable part.
-->

## What & why

<!-- What changed, and why — not just what the diff shows. -->

## Definition-of-done checklist

- [ ] `make verify` is green locally (lint · type · test ≥85% · security · a11y · eval · i18n,
      incl. the eval-baseline regression check and the methods.md writeup check).
- [ ] Tests added/updated for the change, including the identity invariants (sourced-not-inferred,
      unknown-never-penalized, female-fronted-distinct-from-gender) if a read or ranking path was
      touched.
- [ ] Every recommendation surface still shows why + identity basis + source.
- [ ] Docs updated to match: `CHANGELOG.md` `[Unreleased]`, `docs/ROADMAP.md`, a new ADR under
      `docs/adr/` if this is an architectural/process decision, and any affected
      `docs/audits/*.md` artifact.
- [ ] No secrets added; no scraped/bulk musician-identity data added or redistributed.

## Rollback

<!-- How to revert this safely if it needs to come out. Required for changes touching the
     identity resolver, the re-rank, the cache schema (migrations are forward-only), or
     CI/release configuration. -->

## ISO 25010 quality attributes touched (if any)

<!-- e.g. performance, security, maintainability, usability/accessibility, reliability — name
     which one(s) this PR is meant to move, if any, and how you'd notice a regression. -->
