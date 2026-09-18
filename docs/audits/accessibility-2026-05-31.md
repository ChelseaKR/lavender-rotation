# Accessibility Audit — 2026-05-31

> Instantiates RESPONSIBLE-TECH-AUDITS §E (WCAG 2.2 AA).
> **Last verified: 2026-05-31 · Recheck cadence: per UI change / WCAG revision.**

## Automated gate (the mechanical 30–40%)

`make a11y` audited a render of the live recommendation set at `docs/audits/dashboard.html` (as of #71 it no longer regenerates that file — `make render` does, and `tests/test_committed_render.py` byte-gates it)
(`app/build_static.py`, `app/render.py`) and audits it:

- **CI / when available:** `pa11y --runner axe` → **0 violations** required.
- **Offline fallback:** the dependency-free `app/a11y_check.py` checks the
  mechanical subset (lang, viewport, single `h1`, heading order, `<main>` landmark,
  skip link, table caption + `th[scope]`, non-empty link text, `img[alt]`).

Result on this build: **0 violations** (`tests/test_a11y.py` asserts the same
contract in the unit suite, so regressions fail fast).

## Design decisions audited

| Requirement | How it's met |
|-------------|--------------|
| Keyboard-complete | semantic HTML, visible focus (`:focus` outline), skip link to `#main` |
| Charts have data-table equivalents | every score "chart" ships a `<table>` with `<caption>` + `th[scope]` |
| Identity never color-only | identity is rendered as **text** ("Identity: …") + a glyph, not a color |
| 200% zoom / 320px reflow | `max-width` content column + viewport meta; no fixed widths |
| Reduced motion | `@media (prefers-reduced-motion: reduce)` disables transitions |
| Screen-reader friendly cards | `<article aria-labelledby>`, real headings, lists, links |

## Manual walkthrough (review-gated sign-off)

The mechanical gate is necessary, not sufficient. The following manual passes are
required before a release and recorded here:

- [ ] **Keyboard-only**: tab to the lens slider, adjust it, reach every source link.
- [ ] **Screen reader** (VoiceOver/NVDA): card heading → identity → why → sources
      read in a sensible order; unknown state announced respectfully.
- [ ] **200% zoom & 320px**: no horizontal scroll, no clipped content.
- [ ] **Contrast**: text and focus indicators meet AA.

*Sign-off:* _pending first release_ (auto-gate green as of 2026-05-31).

## Enforcement summary

| Commitment | Gate | Where |
|------------|------|-------|
| 0 automated a11y violations | auto | `make a11y`, `tests/test_a11y.py` |
| Keyboard path + chart-table + non-color identity | auto | `app/a11y_check.py`, `tests/test_a11y.py` |
| Screen-reader walkthrough | review | checklist above, per release |
