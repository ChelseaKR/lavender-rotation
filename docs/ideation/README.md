# Ideation — Large-Scale Fixes & Expansions

> Drafted 2026-07-01. Ideas for evaluation, **not commitments**. Nothing here is
> scheduled, resourced, or promised; each item is a candidate to be accepted,
> reshaped, or rejected on its merits.

## What this folder is

A net-new ideation layer on top of the repo's existing planning documents:

- [`docs/ROADMAP.md`](../ROADMAP.md) — the original build spec (M0–M6, all
  implemented; see its build logs of 2026-05-31 and 2026-06-29).
- `docs/RESEARCH-ROADMAP.md` / `docs/USER-RESEARCH.md` — the portfolio-wide
  2026-06-30 synthetic-stakeholder research pass. **Honest note: as of
  2026-07-01 these two files do not exist in this repo** (verified by listing
  `docs/`). Other portfolio repos have them; this one appears to have been
  skipped. That gap is itself recorded as a sequencing note in
  [`04-impact-and-sequencing.md`](./04-impact-and-sequencing.md).

This folder deliberately does **not** restate anything already in ROADMAP.md
(e.g., the "Should: ListenBrainz collaborative signal / thumbs feedback" items
or the "Could: additional sourced value lenses / discovery report / acoustic
features" items). Where an idea builds on an existing roadmap item, it cites
that item by name and describes only the part that goes beyond it.

## Contents

| File | What it holds |
|------|----------------|
| [`01-deep-dive.md`](./01-deep-dive.md) | Current-state assessment from a full read of the source, tests, CI, and audit docs — strengths, structural debt, and the repo's strategic position in the portfolio. |
| [`02-large-scale-fixes.md`](./02-large-scale-fixes.md) | FIX-01…FIX-14: deep structural fixes (live-path completion, ingest scale, entity resolution, fairness metrics, egress hardening, a11y parity, and more). |
| [`03-expansions.md`](./03-expansions.md) | EXP-01…EXP-14 across three horizons: deepen the core (H1), adjacent capabilities (H2), transformative bets (H3). |
| [`04-impact-and-sequencing.md`](./04-impact-and-sequencing.md) | Impact×effort matrix over every FIX/EXP ID, dependencies, a Now/Next/Later sequence beyond the existing roadmap, and the items gated on humans or real data. |

## Ground rules these ideas honor

Every idea in this folder was checked against the repo's non-negotiables
(README "Hard guardrails"; `pipeline/models.py` invariants):

1. **Sourced, never inferred.** No idea introduces any inference path for
   identity. "Unknown" stays first-class and is never penalized.
2. **Values lens made explicit.** Anything that changes ranking must remain
   visible, explained, and boost-only (or provably identity-blind).
3. **Privacy/egress discipline.** New egress is opt-in, user-initiated,
   minimal, and isolated behind an auditable boundary, as `export/` is today.
4. **Accessibility.** New surfaces inherit the WCAG 2.2 AA floor and the
   honest deferred-item practice (the manual screen-reader walkthrough remains
   a review gate — deferred and reported, never faked).
