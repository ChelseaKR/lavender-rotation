"""Browser-driven accessibility specs (roadmap M8 · A11Y-02/07/08/09).

The static checker (``app/a11y_check.py``) and the pa11y/axe renders cover the
mechanical WCAG 2.2 AA subset. These specs graduate three judgment-call
criteria to *observed behaviour* in a real browser engine:

* **Keyboard** (2.1.1 / 2.1.2 / 2.4.3 / 2.4.7): sequential Tab reaches every
  interactive element in DOM order with no trap, focus is always visible, the
  skip link is the first stop and actually jumps to ``<main>``, and the
  data-table scroll regions can be scrolled with Arrow keys alone.
* **Reflow** (1.4.10): at a 320 CSS px viewport the page never scrolls
  horizontally; only the excepted two-dimensional data-table regions scroll,
  inside their own keyboard-focusable wrappers.
* **Reduced motion** (2.3.3, plus 2.2.2's no-autoplay): the stylesheet ships a
  ``prefers-reduced-motion: reduce`` override that zeroes animation and
  transition, and no animation runs in either preference state.

Explicitly NOT covered here: the manual screen-reader/keyboard walkthrough
sign-off (M5, human-only), the Streamlit dashboard (stock components, recorded
for M5), and Lighthouse CI (still absent — see docs/audits/accessibility notes).

The specs need a real Chrome/Chromium. When none is reachable they *skip* with
an actionable message; CI exports ``LAVENDER_E2E_REQUIRE=1`` so a missing browser
there is a hard failure instead of a silent gate-weakening skip (the A11Y-03
"local and CI diverge in strictness" lesson).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from app.build_static import build

if TYPE_CHECKING:  # playwright is an optional (e2e group) dependency
    from playwright.sync_api import Browser, Page

pytestmark = pytest.mark.e2e

_REQUIRE = os.environ.get("LAVENDER_E2E_REQUIRE") == "1"

#: Focus outline contract from ``app/render.py``'s stylesheet: 3px solid, both
#: schemes ≥ 3:1 against the background (see ``tests/test_contrast.py``).
_MIN_OUTLINE_PX = 3.0


def _unavailable(reason: str) -> None:
    if _REQUIRE:
        pytest.fail(f"LAVENDER_E2E_REQUIRE=1 but {reason}", pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="session")
def dashboard_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The shipped (scheme-auto) static render, built fresh from the demo world."""
    out = tmp_path_factory.mktemp("e2e-a11y") / "dashboard.html"
    return build(out=out)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        _unavailable("playwright is not installed (uv sync --group e2e)")
    with sync_playwright() as p:
        launched = None
        errors: list[str] = []
        # Prefer the system Chrome (present on dev Macs and GitHub runners —
        # no browser download); fall back to a bundled Chromium if one has
        # been installed via `playwright install chromium`.
        for kwargs in ({"channel": "chrome"}, {}):
            try:
                launched = p.chromium.launch(headless=True, **kwargs)
                break
            except Error as exc:
                errors.append(str(exc).splitlines()[0])
        if launched is None:
            _unavailable(f"no Chrome/Chromium reachable: {errors}")
        assert launched is not None
        yield launched
        launched.close()


@pytest.fixture
def page(browser: Browser, dashboard_file: Path) -> Iterator[Page]:
    pg = browser.new_page(viewport={"width": 1280, "height": 800})
    pg.goto(dashboard_file.as_uri())
    yield pg
    pg.close()


@pytest.fixture
def narrow_page(browser: Browser, dashboard_file: Path) -> Iterator[Page]:
    """A 320 CSS px viewport — WCAG 1.4.10's reflow breakpoint (~400% zoom)."""
    pg = browser.new_page(viewport={"width": 320, "height": 800})
    pg.goto(dashboard_file.as_uri())
    yield pg
    pg.close()


def _active(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
            const el = document.activeElement;
            const cs = getComputedStyle(el);
            const box = el.getBoundingClientRect();
            return {
                tag: el.tagName.toLowerCase(),
                cls: el.className || "",
                href: el.getAttribute("href") || "",
                label: el.getAttribute("aria-label") || "",
                outlineStyle: cs.outlineStyle,
                outlineWidth: parseFloat(cs.outlineWidth || "0"),
                x: box.x, y: box.y,
            };
        }"""
    )


# ---------------------------------------------------------------------------
# Keyboard (A11Y-02 · WCAG 2.1.1, 2.1.2, 2.4.3, 2.4.7)
# ---------------------------------------------------------------------------


def test_first_tab_stop_is_a_visible_working_skip_link(page: Page) -> None:
    page.keyboard.press("Tab")
    active = _active(page)
    assert active["cls"] == "skip" and active["href"] == "#main"
    # Visible when focused: on-screen (the resting state parks it off-screen)…
    assert active["x"] >= 0 and active["y"] >= 0
    # …and carrying the contract focus indicator (2.4.7).
    assert active["outlineStyle"] == "solid"
    assert active["outlineWidth"] >= _MIN_OUTLINE_PX
    # Activating it jumps to <main> (bypass blocks, 2.4.1).
    page.keyboard.press("Enter")
    assert page.evaluate("location.hash") == "#main"


def test_tab_reaches_every_interactive_element_in_dom_order(page: Page) -> None:
    expected: list[dict[str, str]] = page.evaluate(
        """() => [...document.querySelectorAll('a[href], [tabindex="0"]')].map(el => ({
            tag: el.tagName.toLowerCase(),
            href: el.getAttribute('href') || '',
            label: el.getAttribute('aria-label') || '',
        }))"""
    )
    assert len(expected) > 3, "render lost its interactive elements — spec input invalid"
    seen: list[dict[str, str]] = []
    previous: dict[str, Any] | None = None
    for _ in expected:
        page.keyboard.press("Tab")
        active = _active(page)
        # No keyboard trap (2.1.2): every Tab press must move focus.
        assert active != previous, f"focus stuck on {active}"
        # Focus is always visible (2.4.7): the 3px contract outline.
        assert active["outlineStyle"] == "solid", f"no visible focus on {active}"
        assert active["outlineWidth"] >= _MIN_OUTLINE_PX
        seen.append({"tag": active["tag"], "href": active["href"], "label": active["label"]})
        previous = active
    # Complete and in DOM order (2.1.1 + 2.4.3).
    assert seen == expected


def test_table_scroll_regions_are_keyboard_operable(narrow_page: Page) -> None:
    """The 2-D table regions must be usable without a pointer (2.1.1).

    At 320 px the score table genuinely overflows its wrapper; Tab must reach
    the wrapper, show focus, and Arrow keys must scroll it.
    """
    overflowing = narrow_page.evaluate(
        """() => [...document.querySelectorAll('.table-scroll')]
                 .filter(d => d.scrollWidth > d.clientWidth).length"""
    )
    assert overflowing >= 1, "expected at least one overflowing table region at 320px"
    for _ in range(40):
        narrow_page.keyboard.press("Tab")
        active = _active(narrow_page)
        if active["cls"] == "table-scroll":
            break
    else:
        pytest.fail("Tab never reached a .table-scroll region")
    assert active["outlineStyle"] == "solid"
    assert active["label"], "scroll region must carry an accessible name"
    overflows = "document.activeElement.scrollWidth > document.activeElement.clientWidth"
    if narrow_page.evaluate(overflows):
        for _ in range(5):
            narrow_page.keyboard.press("ArrowRight")
        # Keyboard scrolling animates asynchronously in Chromium — poll, don't race.
        narrow_page.wait_for_function("document.activeElement.scrollLeft > 0", timeout=5000)


# ---------------------------------------------------------------------------
# Reflow (A11Y-07/08 · WCAG 1.4.10)
# ---------------------------------------------------------------------------


def test_no_page_level_horizontal_scroll_at_320px(narrow_page: Page) -> None:
    metrics = narrow_page.evaluate(
        """() => ({
            sw: document.scrollingElement.scrollWidth,
            cw: document.scrollingElement.clientWidth,
        })"""
    )
    assert metrics["sw"] <= metrics["cw"], (
        f"page scrolls horizontally at 320px: scrollWidth {metrics['sw']} > "
        f"clientWidth {metrics['cw']} — reflow (WCAG 1.4.10) regressed"
    )
    # The exception is scoped: 2-D data tables scroll inside their own
    # focusable wrappers, which must still exist for the tables shipped.
    tables = narrow_page.evaluate("document.querySelectorAll('table').length")
    wrapped = narrow_page.evaluate(
        "document.querySelectorAll('.table-scroll[tabindex=\"0\"] > table').length"
    )
    assert tables >= 1 and wrapped == tables, (
        f"{tables} tables but {wrapped} wrapped in focusable scroll regions"
    )


# ---------------------------------------------------------------------------
# Reduced motion (A11Y-09 · WCAG 2.3.3, plus 2.2.2 no-autoplay)
# ---------------------------------------------------------------------------


def test_stylesheet_ships_a_reduced_motion_override(page: Page) -> None:
    rule_text = page.evaluate(
        """() => [...document.styleSheets]
                .flatMap(s => [...s.cssRules])
                .filter(r => r.media && r.media.mediaText.includes('prefers-reduced-motion'))
                .map(r => r.cssText).join('\\n')"""
    )
    assert "reduce" in rule_text, "no prefers-reduced-motion override in the shipped CSS"
    # Chromium expands the `animation: none` shorthand in cssText, so match the
    # semantics (an animation/transition value of none) rather than the shorthand.
    assert re.search(r"animation:[^;]*\bnone\b[^;]*!important", rule_text), rule_text
    assert re.search(r"transition:[^;]*\bnone\b[^;]*!important", rule_text), rule_text


def test_no_animation_runs_in_either_motion_preference(page: Page) -> None:
    for preference in ("reduce", "no-preference"):
        page.emulate_media(reduced_motion=preference)
        assert page.evaluate("document.getAnimations().length") == 0, (
            f"animations running under prefers-reduced-motion: {preference}"
        )
    page.emulate_media(reduced_motion="reduce")
    duration = page.evaluate("getComputedStyle(document.querySelector('a')).transitionDuration")
    assert duration == "0s"
