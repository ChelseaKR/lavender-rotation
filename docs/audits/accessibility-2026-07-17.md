# Accessibility Audit — 2026-07-17 (browser-spec graduation pass)

> Instantiates RESPONSIBLE-TECH-AUDITS §E (WCAG 2.2 AA). Supersedes
> `accessibility-2026-07-09.md` for the automated gate; the manual walkthrough
> section below remains **pending** and carries over unchanged.
> **Last verified: 2026-08-14 · Recheck cadence: per UI change / WCAG revision.**

## What changed since 2026-07-09

Roadmap M8's "Playwright keyboard/reflow/reduced-motion specs" item landed:
three judgment-call criteria that the static checker and axe could only
approximate are now asserted as **observed behaviour in real Chrome**
(`tests/test_e2e_a11y.py`, run inside `make test` / `make verify`, dedicated
entry `make a11y-e2e`; CI sets `LAVENDER_E2E_REQUIRE=1` so a missing browser is a
hard failure there, not a silent skip — the A11Y-03 lesson).

| Spec | WCAG 2.2 | What is observed |
|------|----------|------------------|
| Keyboard | 2.1.1, 2.1.2, 2.4.1, 2.4.3, 2.4.7 | First Tab stop is the skip link, visible on-screen with the 3 px contract outline; Enter jumps to `<main>`; sequential Tab reaches **every** interactive element in DOM order with no trap and visible focus at each stop; the table scroll regions are reachable and Arrow-key operable |
| Reflow | 1.4.10 | At a 320 CSS px viewport the page never scrolls horizontally; every `<table>` sits inside a keyboard-focusable, labelled scroll region (the excepted 2-D content scrolls, the page does not) |
| Reduced motion | 2.3.3 (+ 2.2.2) | The shipped stylesheet carries a `prefers-reduced-motion: reduce` override zeroing animation/transition, and `document.getAnimations()` is empty under both preference states |

## Defect found and fixed by the new specs (honesty note)

The 2026-07-09 audit's design-decision table claimed *"200% zoom / 320px
reflow: `max-width` content column + viewport meta; no fixed widths"*. The new
reflow spec **disproved that claim as previously stated**: at 320 px the
score-summary table forced page-level horizontal scrolling (observed
`scrollWidth` 445 vs `clientWidth` 320) — real 1.4.10 non-conformance that axe
does not flag. Fixed in the same change (`app/render.py`): each data table is
wrapped in `<div class="table-scroll" role="region" tabindex="0"
aria-label=…>` with `overflow-x: auto` and the 3 px focus outline, so only the
WCAG-excepted two-dimensional content scrolls, Arrow keys operate it after
Tab-focusing (verified behaviourally), and long citation URLs wrap
(`overflow-wrap: anywhere`). Page-level `scrollWidth` now equals
`clientWidth` at 320 px.

## Automated gate summary (this build, macOS Dark-Mode host)

- `make a11y`: pa11y/axe **0 violations × 3 renders** (auto, light-pinned,
  dark-pinned) — unchanged.
- `make a11y-e2e`: **6/6 browser specs pass** in headless Chrome
  (keyboard ×3, reflow ×1, reduced-motion ×2).
- `tests/test_contrast.py` token ratios unchanged (no new colours; the scroll
  region reuses the existing `--focus` outline token, ≥ 3:1 both schemes).

## Still NOT covered by any automated gate (unchanged, deliberate)

- **Manual screen-reader + keyboard walkthrough sign-off (M5)** — ⛔ human-only,
  still pending; must be performed by a person and dated when actually done.
  The checklist from `accessibility-2026-07-09.md` carries over verbatim,
  including both OS themes; its "320px: no horizontal scroll" line item now has
  an automated floor, but the human pass remains required.
- **Lighthouse CI** — not wired; recorded here rather than claimed.
- **The Streamlit dashboard** — stock components; status belongs in
  `docs/a11y/STATEMENT.md` alongside the M5 walkthrough. That file does not
  exist yet — this is a forward reference to where the status will live, not a
  link to an audit that has been done. `app/dashboard.py` is also the one module
  still in `[tool.coverage.run] omit`: 0% covered, verified by neither the a11y
  gate (which this section excludes it from) nor the walkthrough (still pending
  above). Recorded as an open gap rather than described as verified (#71,
  2026-08-14). The other three `app/` modules were removed from that omit in the
  same change and measure 82–100%.

## The committed render is now gated (2026-08-14, #71)

`docs/audits/dashboard.html` — the artifact this audit names as what was
audited — is a committed, publicly browsable deliverable that nothing
regenerated and nothing checked. `make a11y` overwrote it and then audited the
fresh copy, so the gate could never observe that the committed page had drifted;
on `main` it was still displaying three MusicBrainz citations that locate no
record, and their matching `/edit` links, months after PR #66 fixed the fixture.

`tests/test_committed_render.py` now asserts the committed bytes equal what
`app/build_static.py` renders today (stage 3, before the a11y stage can
overwrite anything), that the render is deterministic, and that every citation
it displays locates a record. Regeneration moved out of `make a11y` into an
explicit `make render`.

*Sign-off:* _pending_ (automated gates green as of 2026-08-14; human
walkthrough still required).
