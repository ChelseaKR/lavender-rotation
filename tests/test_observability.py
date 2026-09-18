"""Display adapter tests for fairness observability (EXP-01)."""

from __future__ import annotations

import pytest
from app.observability import OBSERVABILITY_K, observability_inputs, recommendations_by_lens
from recommender.exposure import SEGMENTS, exposure_at_k, observability_panel
from recommender.hybrid import recommend


def _rankings(profile, catalog, source):
    return {
        lens: recommend(profile, catalog, source, k=len(catalog), lens_strength=lens)
        for lens in (0.0, 0.25, 0.5, 0.75, 1.0)
    }


def test_panel_aligns_rows_and_retention(profile, catalog, source) -> None:
    panel = observability_panel(_rankings(profile, catalog, source), 1.0, k=3)
    assert [row["segment"] for row in panel["exposure_rows"]] == list(SEGMENTS)
    assert panel["retention_row"]["segment"] == "unknown"
    assert all(value == 1.0 for value in panel["retention_row"]["by_lens"].values())


def test_panel_exposure_preserves_protected_demo_top_k(profile, catalog, source) -> None:
    panel = observability_panel(_rankings(profile, catalog, source), 1.0, k=3)
    # The demo's top three include an unknown artist in slot 2.  The eligible
    # artists receive boosts, but none can cross that protected slot, so the
    # top-three segment shares intentionally stay unchanged.
    assert all(row["base_share"] == row["current_share"] for row in panel["exposure_rows"])


def test_panel_rejects_missing_lenses(profile, catalog, source) -> None:
    rankings = _rankings(profile, catalog, source)
    with pytest.raises(ValueError, match="current_lens"):
        observability_panel(rankings, 0.9, k=5)
    with pytest.raises(ValueError, match="base_lens"):
        observability_panel(rankings, 1.0, k=5, base_lens=0.9)


# --- #114: the panel must measure the ranking the page is showing -------------
#
# The Streamlit dashboard's displayed list carried the Serendipity slider's
# `explore` value and its `recs_by_lens` recompute did not. Every other argument
# matched, so it was an omission rather than a different question by design, and
# the page published "Base share"/"Current share" for a ranking it was not
# showing: at `explore=1.0` on the demo world it reported no sourced man in a
# top-3 slot while a sourced man sat in slot 3 of the same screen.
#
# `app/dashboard.py` is excluded from coverage (a live `streamlit run` entry
# point), so these call the same importable seam the dashboard now calls.

_EXPLORE_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)


@pytest.mark.parametrize("explore", _EXPLORE_GRID)
@pytest.mark.parametrize("lens", [0.0, 0.5, 1.0])
def test_the_panel_measures_the_list_it_is_shown_beside(
    profile, catalog, source, explore, lens
) -> None:
    """ "Current share" is the displayed ranking's exposure, at every slider setting."""
    # What a surface would put on screen for these knobs, asked for directly —
    # this is the call the dashboard made, and the panel is what it printed
    # beside it.
    displayed = recommend(profile, catalog, source, k=10, lens_strength=lens, explore=explore)

    recs, panel = observability_inputs(
        profile, catalog, source, current_lens=lens, k=10, explore=explore
    )

    assert recs == displayed, "the seam returned a different list than it would display"
    stated = {row["segment"]: row["current_share"] for row in panel["exposure_rows"]}
    assert stated == pytest.approx(exposure_at_k(displayed, OBSERVABILITY_K)), (
        "the fairness panel is describing a ranking the page is not showing"
    )


@pytest.mark.parametrize("explore", _EXPLORE_GRID)
def test_the_displayed_list_is_the_swept_list(profile, catalog, source, explore) -> None:
    """`recs_by_lens[current_lens]` *is* what the caller displays, not a second list."""
    recs, _panel = observability_inputs(
        profile, catalog, source, current_lens=0.5, k=10, explore=explore
    )
    sweep = recommendations_by_lens(
        profile, catalog, source, current_lens=0.5, k=10, explore=explore
    )
    assert [r.artist.artist_id for r in recs] == [r.artist.artist_id for r in sweep[0.5]]
    assert recs == recommend(profile, catalog, source, k=10, lens_strength=0.5, explore=explore), (
        "a separate recommend() call with the same knobs must return the same list"
    )


def test_the_diversifier_does_move_top_k_exposure_on_this_world() -> None:
    """EXP-04's excellence bar, measured rather than assumed.

    `docs/ideation/03-expansions.md` EXP-04 sets the bar at "identity-segment
    exposure (FIX-05) statistically unchanged by the diversifier at any
    setting". At k=3 on the demo world that is false, which is exactly why the
    panel could not be left computing a lens-only sweep and captioned as though
    it described the picks on screen. This test pins the fact down so the claim
    cannot be quietly restored: if the diversifier ever does become
    exposure-neutral here, this fails and the doc's status note can be revisited.

    `tests/test_diversify.py` still proves the pass is permutation-not-rescore
    and identity-blind by AST — blindness at the input is not neutrality at k.
    """
    from pipeline.demo import demo_catalog, demo_profile, demo_source

    demo = (demo_profile(), demo_catalog(), demo_source())
    at_zero, _ = observability_inputs(*demo, current_lens=0.5, k=10, explore=0.0)
    at_one, _ = observability_inputs(*demo, current_lens=0.5, k=10, explore=1.0)

    assert exposure_at_k(at_zero, OBSERVABILITY_K) != exposure_at_k(at_one, OBSERVABILITY_K), (
        "if this now holds, EXP-04's bar is met and its recorded status is stale"
    )
