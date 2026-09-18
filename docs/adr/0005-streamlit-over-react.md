# 0005. Streamlit dashboard over a React front end

Date: 2026-07-17

## Status

Accepted (backfilled record of the 2026-05-31 build decision; see ADR 0000 on backfilling)

## Context

The product needs a local, single-user dashboard: enter a username, see ranked "why" cards with
identity basis and sources, adjust the values-lens slider. A React (or other SPA) front end would
add a second language, a build toolchain, and an API layer between the Python pipeline and the
screen — for a data-app-shaped UI maintained by one person. The original decision is recorded in
prose in `docs/ROADMAP.md` §6 ("Streamlit over React — solo speed, data-app shape; React noted as
a later option"); this ADR is its dated, referenceable form.

## Decision

Use Streamlit (`app/dashboard.py`) for the interactive dashboard. Keep the render logic that must
be provable — the accessible HTML of the why cards — in a framework-free static renderer
(`app/render.py`) shared by `lavender report` and the a11y gate, so the merge-blocking pa11y/axe check
does not depend on Streamlit internals.

## Consequences

- One language, no JS build chain; the whole product remains `uv sync && make dev` runnable.
- The a11y gate covers the static render deterministically; Streamlit's own widget DOM is only
  fully assessable by the still-pending human screen-reader/keyboard walkthrough
  (`docs/audits/accessibility-2026-05-31.md`), and its stock dark theme is outside the static
  render's token guarantees.
- A re-platform to React remains an option, but per the reconciled backlog
  (`docs/ideation/02-large-scale-fixes.md`, FIX-09 disposition) it is not justified unless the
  human assistive-technology pass produces findings Streamlit cannot fix.
