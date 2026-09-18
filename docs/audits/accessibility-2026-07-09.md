# Accessibility Audit — 2026-07-09 (two-scheme contrast pass)

> Instantiates RESPONSIBLE-TECH-AUDITS §E (WCAG 2.2 AA). Supersedes
> `accessibility-2026-05-31.md` for the automated gate; the manual walkthrough
> section below remains **pending** and carries over unchanged.
> **Last verified: 2026-07-09 · Recheck cadence: per UI change / WCAG revision.**

## What changed since 2026-05-31

The 2026-07-05 conformance pass discovered (and honestly declined to hide) a real
defect: the static render declared `:root { color-scheme: light dark; }` with **no
explicit colors anywhere**, so under an OS dark theme (e.g. a macOS Dark-Mode
machine, whose headless Chromium inherits `prefers-color-scheme: dark`) the page
fell back to UA default colors and produced **127 axe contrast violations**.
Light-mode CI saw none of this — exactly the A11Y-03 "local and CI silently
diverge in strictness" gap the 2026-07-05 audit flagged.

Fixed 2026-07-09 (roadmap BUG-1):

1. **Explicit two-scheme token set** (`app/render.py::LIGHT_TOKENS/DARK_TOKENS`)
   with computed WCAG 2.2 ratios (light / dark, vs each scheme's `--bg`):
   | Token | Light | Ratio | Dark | Ratio | Threshold |
   |-------|-------|-------|------|-------|-----------|
   | text (body) | `#1b1b1b` on `#ffffff` | 17.22:1 | `#e8e8e8` on `#121212` | 15.29:1 | ≥ 7:1 (AAA target) |
   | link | `#0b57d0` | 6.39:1 | `#8ab4f8` | 8.89:1 | ≥ 4.5:1 (AA) |
   | border (cards, tables) | `#595959` | 7.00:1 | `#9e9e9e` | 6.99:1 | ≥ 3:1 (non-text) |
   | focus outline | `#0b57d0` | 6.39:1 | `#8ab4f8` | 8.89:1 | ≥ 3:1 (non-text) |

   The `.identity` glyph inherits `--text` (paired with the "Identity: …" text —
   meaning is never color-only, unchanged).
2. **Merge-blocking design-token contrast test** (`tests/test_contrast.py`,
   closes the A11Y-05 partial): pure-Python WCAG relative-luminance over every
   declared pair, both palettes, plus render-level checks that the pinned and
   auto stylesheets actually carry the tokens. No new dependency.
3. **Scheme-complete gate**: `make a11y` now renders the shipped
   (`prefers-color-scheme`-responsive) artifact **plus** a light-pinned and a
   dark-pinned variant and requires **0 violations on all three** — so a
   Dark-Mode Mac and light-mode CI audit the same two palettes deterministically.

## Automated gate (the mechanical 30–40%)

`make a11y` audited a render of the live recommendation set at `docs/audits/dashboard.html` (as of #71 `make a11y` no longer regenerates that file — `make render` does)
(auto) + `/tmp/wad-dashboard-{light,dark}.html` (pinned gate inputs) and audits
all three:

- **CI / when available:** `pa11y --runner axe` → **0 violations** required per
  file (light AND dark).
- **Offline fallback:** the dependency-free `app/a11y_check.py` mechanical subset,
  also over all three files.

Result on this build (macOS **Dark Mode** host, i.e. the environment that
previously failed): **0 violations × 3 renders** via pa11y/axe;
`tests/test_a11y.py` + `tests/test_contrast.py` assert the same contracts in the
unit suite.

## Design decisions audited

| Requirement | How it's met |
|-------------|--------------|
| Keyboard-complete | semantic HTML, visible focus (`:focus` outline ≥ 3:1 in both schemes), skip link to `#main` |
| Charts have data-table equivalents | every score "chart" ships a `<table>` with `<caption>` + `th[scope]` |
| Identity never color-only | identity is rendered as **text** ("Identity: …") + a glyph inheriting text color |
| Contrast, both schemes | explicit light+dark token pairs, unit-tested ratios (table above), pa11y on pinned renders |
| 200% zoom / 320px reflow | `max-width` content column + viewport meta; no fixed widths |
| Reduced motion | `@media (prefers-reduced-motion: reduce)` disables transitions |
| Screen-reader friendly cards | `<article aria-labelledby>`, real headings, lists, links |

Out of this gate's scope, recorded for M5: the Streamlit dashboard's own dark
theme is stock-component; its status belongs in `docs/a11y/STATEMENT.md` when
that file is created alongside the human walkthrough.

## Manual walkthrough (review-gated sign-off — ⛔ HUMAN-ONLY)

The mechanical gate is necessary, not sufficient. The following manual passes are
required before a release and recorded here. They must be performed by a person;
this pass deliberately did not (and must never) fabricate them. Now that dark
mode has explicit tokens, the walkthrough should cover **both** OS themes.

- [ ] **Keyboard-only**: tab to the lens slider, adjust it, reach every source link.
- [ ] **Screen reader** (VoiceOver/NVDA): card heading → identity → why → sources
      read in a sensible order; unknown state announced respectfully.
- [ ] **200% zoom & 320px**: no horizontal scroll, no clipped content.
- [ ] **Contrast spot-check in both OS themes**: text and focus indicators meet AA
      visually (the computed gate above is the floor, not the ceiling).

*Sign-off:* _pending_ (auto-gate green in both schemes as of 2026-07-09; human
walkthrough still required — sequence it after this fix so dark mode is covered).

## Enforcement summary

| Commitment | Gate | Where |
|------------|------|-------|
| 0 automated a11y violations, light AND dark | auto | `make a11y` (3 renders), `tests/test_a11y.py` |
| Token contrast ≥ 7:1 text / ≥ 4.5:1 links / ≥ 3:1 non-text, both palettes | auto | `tests/test_contrast.py` |
| Keyboard path + chart-table + non-color identity | auto | `app/a11y_check.py`, `tests/test_a11y.py` |
| Screen-reader walkthrough | review (human) | checklist above, per release |
