"""The Streamlit dashboard's two fairness tables, built and read without a Streamlit runtime.

**This file exists because nothing rendered them.** `tests/test_committed_render.py` holds the
*static* render (`app/render.py`) to the byte, and `tests/test_observability.py` holds the panel
`app/dashboard.py` reads. Between those two the dashboard's own table construction was
unexercised, so when `segment_retention` started reporting `None` for an absent segment (#129)
and `app/render.py` was taught to say so, the interactive dashboard was left formatting a value
that no longer existed:

    TypeError: unsupported format string passed to NoneType.__format__

on the **demo world**, which is the only world that dashboard ever shows. `mypy --strict` could
not see it: the expression carried a ``cast("dict[str, float]", ...)`` asserting a type the value
had stopped having, and a cast is a promise the checker takes at its word.

So the tables are built by named functions now, and these tests call them over the real demo
world rather than a fixture shaped to be convenient.
"""

from __future__ import annotations

from typing import cast

import pytest
from app.dashboard import (
    UNMEASURED_TEXT,
    fairness_exposure_table,
    fairness_retention_table,
    unmeasured_or_percent,
)
from app.observability import OBSERVABILITY_K, observability_inputs
from pipeline.demo import DEMO_USER, demo_catalog, demo_scrobbles, demo_source
from pipeline.ingest import build_profile
from recommender.exposure import SEGMENTS, exposure_at_k


def _demo_panel() -> dict[str, object]:
    profile = build_profile(DEMO_USER, demo_scrobbles())
    _recs, panel = observability_inputs(
        profile,
        demo_catalog(),
        demo_source(),
        current_lens=1.0,
        k=10,
        panel_k=OBSERVABILITY_K,
    )
    return panel


def _rows(panel: dict[str, object], key: str) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", panel[key])


def test_the_demo_world_renders_both_fairness_tables() -> None:
    """The regression, in the world the dashboard actually shows.

    The demo catalog holds no artist sourced as ``Gender.OTHER``, so `other_retention` is
    ``None`` for every lens. This raised ``TypeError`` before the tables were formatted through
    a helper that knows what ``None`` means.
    """
    panel = _demo_panel()
    retention_rows = _rows(panel, "retention_rows")
    lens_keys = list(cast("dict[str, object]", retention_rows[0]["by_lens"]))

    exposure = fairness_exposure_table(_rows(panel, "exposure_rows"))
    retention = fairness_retention_table(retention_rows, lens_keys)

    assert exposure["Identity segment"] == list(SEGMENTS)
    assert retention["Identity segment"] == ["unknown", "other"]
    # Every cell is a string a table can show, and none of them is a stringified None.
    for table in (exposure, retention):
        for column in table.values():
            assert all(isinstance(cell, str) for cell in column)
            assert "None" not in column


def test_an_absent_segment_reads_as_not_measured_rather_than_as_a_number() -> None:
    """`other` has no artist in the demo world, so its retention is not a figure at all.

    Rendering it as ``100%`` -- which the equivalent static cell did until #129 -- tells a reader
    the strongest version of a claim nobody checked, on the panel whose purpose is to make the
    claim checkable. ``0%`` would be the opposite lie.
    """
    panel = _demo_panel()
    retention_rows = _rows(panel, "retention_rows")
    lens_keys = list(cast("dict[str, object]", retention_rows[0]["by_lens"]))
    retention = fairness_retention_table(retention_rows, lens_keys)

    other_index = cast("list[object]", retention["Identity segment"]).index("other")
    for column, cells in retention.items():
        if column == "Identity segment":
            continue
        assert cells[other_index] == UNMEASURED_TEXT
        assert cells[other_index] not in ("100%", "0%")


def test_the_helper_never_turns_an_unmeasured_value_into_a_percentage() -> None:
    """Written against literals: the point is which strings can and cannot come out."""
    assert unmeasured_or_percent(None) == "not measured"
    assert unmeasured_or_percent(0.0) == "0%"
    assert unmeasured_or_percent(1.0) == "100%"
    assert unmeasured_or_percent(0.3333) == "33%"


def test_an_empty_top_k_has_no_exposure_shares_to_render() -> None:
    """The second nullable figure the same table carries.

    Over an empty top-k the shares used to be ``0.0`` for every segment -- a set of shares that
    sums to zero, which no distribution does. The table now says so instead of printing seven
    zeroes that read as a measured ranking.
    """
    shares = exposure_at_k([], k=5)
    assert set(shares) == set(SEGMENTS)
    assert set(shares.values()) == {None}

    rows = [
        {"segment": segment, "base_share": shares[segment], "current_share": shares[segment]}
        for segment in SEGMENTS
    ]
    table = fairness_exposure_table(rows)
    assert set(cast("list[object]", table["Base share"])) == {UNMEASURED_TEXT}
    assert set(cast("list[object]", table["Current share"])) == {UNMEASURED_TEXT}


def test_a_populated_top_k_still_publishes_shares_that_sum_to_one() -> None:
    """The control: the null path must not have swallowed the measuring one."""
    profile = build_profile(DEMO_USER, demo_scrobbles())
    _recs, panel = observability_inputs(
        profile,
        demo_catalog(),
        demo_source(),
        current_lens=1.0,
        k=10,
        panel_k=OBSERVABILITY_K,
    )
    shares = [cast(float, row["base_share"]) for row in _rows(panel, "exposure_rows")]
    assert all(value is not None for value in shares)
    # The tolerance is the module's own `round(..., 4)`, not slack: at this k three segments
    # each hold a third of the slots, so the published shares are 0.3333 and sum to 0.9999.
    # Half a unit in the last place per segment is exactly what that rounding can cost.
    assert sum(shares) == pytest.approx(1.0, abs=len(SEGMENTS) * 5e-5)
    assert sum(shares) > 0.99
