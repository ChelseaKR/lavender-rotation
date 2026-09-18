"""Design-token contrast gate (BUG-1 / A11Y-05): both schemes, computed, blocking.

The dark-mode contrast defect happened because ``color-scheme: light dark`` was
declared with no explicit colors — nothing *verified* the pairs that actually
rendered. This test computes WCAG 2.2 contrast (relative luminance, SC 1.4.3 /
1.4.6 / 1.4.11) over the declared design tokens for BOTH palettes, so a palette
edit that breaks either scheme fails the unit suite before pa11y ever runs.
No new dependency: the math is the WCAG definition, ~10 lines of stdlib.
"""

from __future__ import annotations

import pytest
from app.render import DARK_TOKENS, LIGHT_TOKENS, render_cards_html
from recommender.hybrid import recommend

# WCAG 2.2 thresholds. Body text targets AAA (7:1) per the roadmap spec; links
# are held to AA text contrast (4.5:1); borders and focus indicators are
# non-text contrast (1.4.11, 3:1).
TEXT_AAA = 7.0
TEXT_AA = 4.5
NON_TEXT = 3.0


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("tokens", [LIGHT_TOKENS, DARK_TOKENS], ids=["light", "dark"])
def test_body_text_meets_aaa_contrast(tokens) -> None:
    assert contrast_ratio(tokens["text"], tokens["bg"]) >= TEXT_AAA


@pytest.mark.parametrize("tokens", [LIGHT_TOKENS, DARK_TOKENS], ids=["light", "dark"])
def test_link_text_meets_aa_contrast(tokens) -> None:
    assert contrast_ratio(tokens["link"], tokens["bg"]) >= TEXT_AA


@pytest.mark.parametrize("tokens", [LIGHT_TOKENS, DARK_TOKENS], ids=["light", "dark"])
@pytest.mark.parametrize("token", ["border", "focus"])
def test_non_text_indicators_meet_3_to_1(tokens, token) -> None:
    assert contrast_ratio(tokens[token], tokens["bg"]) >= NON_TEXT


def test_both_palettes_declare_the_same_token_set() -> None:
    assert set(LIGHT_TOKENS) == set(DARK_TOKENS)  # no scheme can silently miss a pair


def test_sanity_the_checker_rejects_a_bad_pair() -> None:
    # Guard the guard: near-identical grays must fail, so a broken palette cannot
    # slip through a broken checker.
    assert contrast_ratio("#777777", "#888888") < NON_TEXT


def _render(profile, catalog, source, scheme: str) -> str:
    recs = recommend(profile, catalog, source, k=10, lens_strength=0.5)
    return render_cards_html(recs, lens_strength=0.5, username="demo", scheme=scheme)


def test_auto_render_carries_explicit_tokens_for_both_schemes(profile, catalog, source) -> None:
    html = _render(profile, catalog, source, "auto")
    assert "color-scheme: light dark" in html
    assert "@media (prefers-color-scheme: dark)" in html
    assert LIGHT_TOKENS["text"] in html and DARK_TOKENS["text"] in html


@pytest.mark.parametrize("scheme,tokens", [("light", LIGHT_TOKENS), ("dark", DARK_TOKENS)])
def test_pinned_render_pins_exactly_one_palette(profile, catalog, source, scheme, tokens) -> None:
    html = _render(profile, catalog, source, scheme)
    assert f"color-scheme: {scheme}" in html
    assert "@media (prefers-color-scheme: dark)" not in html  # pinned = unconditional
    assert tokens["text"] in html and tokens["bg"] in html


def test_unknown_scheme_is_rejected(profile, catalog, source) -> None:
    with pytest.raises(ValueError):
        _render(profile, catalog, source, "sepia")
