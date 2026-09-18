"""One place that decides what the fairness panel measures (#114).

Every surface that renders the panel previously built its own ``recs_by_lens``
sweep next to its own displayed list, and the Streamlit dashboard's two calls
had drifted: the displayed list carried the Serendipity slider's ``explore``
value and the sweep did not. Every other argument matched — ``profile``,
``catalog``, ``source``, ``k``, ``feedbacks`` — so it was an omission rather
than a different question by design, and the page ended up publishing "Base
share"/"Current share" for a ranking it was not showing. At the top of the
slider it reported no sourced man in a top-3 slot while a sourced man sat
visibly in slot 3 of the same screen.

The fix is structural rather than one more argument at one call site:
:func:`observability_inputs` returns the sweep **and** the list to display, from
one set of knobs, so a surface cannot show one ranking and measure another. The
whole panel is then computed at the caller's current ``explore`` — including the
``base_lens`` column, which is the honest counterfactual to show beside it: at
your current Serendipity setting, this is what the lens does across strengths.

``app/dashboard.py`` is excluded from coverage (a live ``streamlit run`` entry
point with no import-time seam), which is why this lives here and not there:
the decision is testable even though the surface is not.
"""

from __future__ import annotations

from pipeline.lastfm import ScrobbleSource
from pipeline.models import Artist, ListeningProfile, Recommendation
from recommender.content_filters import NO_FILTER, ContentFilter
from recommender.exposure import observability_panel
from recommender.feedback import Feedback
from recommender.hybrid import recommend
from recommender.lens import VALUES_LENS, LensSpec

#: The lens strengths the retention sweep always covers, plus whichever one the
#: caller is currently showing.
LENS_GRID: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)

#: Top-k the exposure and retention tables are computed over.
OBSERVABILITY_K = 3


def recommendations_by_lens(
    profile: ListeningProfile,
    catalog: dict[str, Artist],
    source: ScrobbleSource,
    *,
    current_lens: float,
    k: int,
    explore: float = 0.0,
    feedbacks: list[Feedback] | None = None,
    hide_sourced_men: bool = False,
    lens: LensSpec = VALUES_LENS,
    lens_grid: tuple[float, ...] = LENS_GRID,
    content_filter: ContentFilter = NO_FILTER,
) -> dict[float, list[Recommendation]]:
    """The lens sweep, with every knob other than ``lens_strength`` held fixed.

    ``current_lens`` is always a key, so ``result[current_lens]`` is the list the
    caller should display — identical by construction to what a separate
    :func:`~recommender.hybrid.recommend` call with these arguments would return.
    """
    return {
        value: recommend(
            profile,
            catalog,
            source,
            k=k,
            lens_strength=value,
            explore=explore,
            feedbacks=feedbacks,
            hide_sourced_men=hide_sourced_men,
            lens=lens,
            # Held fixed across the sweep like every other knob: a panel that
            # compared a filtered list at one lens value with an unfiltered one at
            # another would be measuring the filter and calling it the lens.
            content_filter=content_filter,
        )
        for value in sorted({*lens_grid, current_lens})
    }


def observability_inputs(
    profile: ListeningProfile,
    catalog: dict[str, Artist],
    source: ScrobbleSource,
    *,
    current_lens: float,
    k: int,
    panel_k: int = OBSERVABILITY_K,
    explore: float = 0.0,
    feedbacks: list[Feedback] | None = None,
    hide_sourced_men: bool = False,
    lens: LensSpec = VALUES_LENS,
    lens_grid: tuple[float, ...] = LENS_GRID,
    content_filter: ContentFilter = NO_FILTER,
) -> tuple[list[Recommendation], dict[str, object]]:
    """Return ``(displayed_recs, panel)`` — the list to show and the panel about it.

    Returning both together is the point. A caller that renders the first and
    the second cannot show one ranking while measuring another, which is the
    defect this replaces.
    """
    recs_by_lens = recommendations_by_lens(
        profile,
        catalog,
        source,
        current_lens=current_lens,
        k=k,
        explore=explore,
        feedbacks=feedbacks,
        hide_sourced_men=hide_sourced_men,
        lens=lens,
        lens_grid=lens_grid,
        content_filter=content_filter,
    )
    panel = observability_panel(recs_by_lens, current_lens=current_lens, k=panel_k)
    return recs_by_lens[current_lens], panel
