# 0009. Keep the flat package layout (no `src/` migration)

Date: 2026-07-17

## Status

Accepted (documents the standing deviation from CQ-23; revisit if packaging pain appears)

## Context

`CODE-QUALITY-STANDARD.md` (CQ-23) prefers the `src/` layout, which prevents accidentally
importing the working-copy package instead of the installed one. This repo ships four top-level
packages (`pipeline`, `recommender`, `app`, `export`) in a flat layout. The 2026-07-05 audit
tracked "src/ layout migration (or ADR accepting flat layout)" as open P2-1/P3 backfill work;
this ADR is that record.

## Decision

Keep the flat layout. The failure mode `src/` guards against is already mitigated here:
installation is uv-managed and editable everywhere (local `make install` and CI both run
`uv sync --frozen`, so the imported package *is* the working copy by construction, with hatchling
explicitly listing the four packages in `pyproject.toml`), and the test suite imports the packages
via their installed names with merge-blocking coverage floors that would surface a wrong-package
import immediately. A migration would churn every import path and historical diff for no
behavioral gain in a repo with exactly one distribution and one maintainer.

## Consequences

- CQ-23 remains a documented, dated deviation (per CQ-45) instead of a silent one.
- The top-level namespace carries four generic-sounding package names; the boundary that actually
  matters (network egress confined to named modules) is enforced by `tests/test_privacy.py`, not
  by directory shape.
- Supersede this ADR if the project ever gains a second distribution, a plugin architecture, or
  evidence of the accidental-import failure `src/` exists to prevent.
