"""FIX-05: computed exposure / rank-fairness metrics + the unknown-retention guarantee.

These are the *generated numbers* behind ``docs/audits/fairness-identity.md``: the
fairness narrative, verified on the recommender's real output rather than only on
the rerank function.
"""

from __future__ import annotations

from typing import cast

import pytest
from pipeline.models import (
    Explanation,
    Gender,
    IdentityBasis,
    Recommendation,
    Signal,
)
from recommender.eval import DEFAULT_LENS_SWEEP, fairness_report
from recommender.exposure import (
    FEMALE_FRONTED,
    MAN,
    NONBINARY,
    OTHER,
    SEGMENTS,
    UNKNOWN,
    WOMAN,
    FairnessAssertionError,
    assert_unknown_retained,
    exposure_at_k,
    exposure_report,
    identity_segment,
    popularity_identity_crosstab,
    popularity_tier,
    rank_shift_by_segment,
    segment_base_count,
    unknown_retention,
)
from recommender.hybrid import recommend

from .conftest import make_artist

_ALIGNED = frozenset({WOMAN, NONBINARY, FEMALE_FRONTED})


def _recs_by_lens(profile, catalog, source, lenses=DEFAULT_LENS_SWEEP):
    return {
        lens: recommend(profile, catalog, source, k=len(catalog), lens_strength=lens)
        for lens in lenses
    }


def _rec(artist, score, delta=0.0):
    expl = Explanation(
        signals=(Signal("content", "tag", 1.0),),
        identity_basis=IdentityBasis.UNKNOWN
        if artist.identity.gender is Gender.UNKNOWN
        else IdentityBasis.SELF_IDENTIFIED,
        identity_sources=artist.identity.sources,
        summary="why",
    )
    return Recommendation(artist=artist, base_score=score, rerank_delta=delta, explanation=expl)


# -- segmentation (sourced, never inferred) ----------------------------------
def test_identity_segment_reads_sourced_identity_then_composition(catalog) -> None:
    assert identity_segment(catalog["snail-mail"]) == WOMAN
    assert identity_segment(catalog["shamir"]) == NONBINARY
    assert identity_segment(catalog["moses-sumney"]) == MAN
    assert identity_segment(catalog["boygenius"]) == FEMALE_FRONTED  # sourced composition
    assert identity_segment(catalog["mystery-act"]) == UNKNOWN


def test_other_gender_is_its_own_segment_not_unknown() -> None:
    assert identity_segment(make_artist("x", gender=Gender.OTHER)) == "other"


def test_popularity_tier_boundaries() -> None:
    assert popularity_tier(99_999) == "niche"
    assert popularity_tier(100_000) == "mid"
    assert popularity_tier(999_999) == "mid"
    assert popularity_tier(1_000_000) == "popular"


# -- exposure ----------------------------------------------------------------
def test_exposure_shares_sum_to_one(profile, catalog, source) -> None:
    recs = recommend(profile, catalog, source, k=5, lens_strength=0.0)
    shares = exposure_at_k(recs, k=5)
    assert set(shares) == set(SEGMENTS)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_an_empty_top_k_has_no_shares_rather_than_shares_of_zero() -> None:
    """This test previously asserted the defect, as ``test_exposure_at_k_empty_is_all_zero``.

    ``{0.0}`` across every segment says each of them held none of the slots -- a measurement --
    when what happened is that there were no slots. The arithmetic is the tell: over any real
    top-k these shares are a partition and sum to one, and a set summing to zero is not one.
    """
    shares = exposure_at_k([], k=5)
    assert set(shares) == set(SEGMENTS)
    assert set(shares.values()) == {None}


def test_exposure_metrics_reject_nonpositive_k() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        exposure_at_k([], k=0)
    with pytest.raises(ValueError, match="at least 1"):
        unknown_retention({0.0: []}, k=0)


def test_lens_shifts_exposure_toward_aligned_segments(profile, catalog, source) -> None:
    at0 = exposure_at_k(recommend(profile, catalog, source, k=5, lens_strength=0.0), k=5)
    at1 = exposure_at_k(recommend(profile, catalog, source, k=5, lens_strength=1.0), k=5)
    aligned0 = sum(at0[s] for s in _ALIGNED)
    aligned1 = sum(at1[s] for s in _ALIGNED)
    assert aligned1 >= aligned0  # the lens surfaces more aligned artists, never fewer


# -- the merge-blocking unknown-retention guarantee --------------------------
def test_unknown_retention_is_100pct_at_every_lens(profile, catalog, source) -> None:
    retention = unknown_retention(_recs_by_lens(profile, catalog, source), k=5)
    assert retention  # non-empty sweep
    assert all(value == 1.0 for value in retention.values())


def test_assert_unknown_retained_passes_on_demo_output(profile, catalog, source) -> None:
    assert_unknown_retained(_recs_by_lens(profile, catalog, source), k=5)  # must not raise


def test_unknown_slots_survive_end_to_end_exploration(profile, catalog, source) -> None:
    """Post-rerank MMR must not undo the top-k guarantee at the product boundary."""
    rankings = {
        lens: recommend(
            profile,
            catalog,
            source,
            k=5,
            lens_strength=lens,
            explore=0.03,
        )
        for lens in DEFAULT_LENS_SWEEP
    }

    assert_unknown_retained(rankings, k=5)
    unknown_positions = {
        lens: {
            rec.artist.artist_id: rec.rank
            for rec in recs
            if identity_segment(rec.artist) == UNKNOWN
        }
        for lens, recs in rankings.items()
    }
    assert all(positions == unknown_positions[0.0] for positions in unknown_positions.values())
    assert unknown_positions[0.0] == {"mystery-act": 2}


def test_assert_unknown_retained_detects_a_dropped_unknown() -> None:
    unknown = make_artist("mystery", gender=Gender.UNKNOWN)
    woman = make_artist("w", gender=Gender.WOMAN)
    base = [_rec(unknown, 0.5), _rec(woman, 0.4)]
    boosted = [_rec(woman, 0.4, delta=0.5)]  # unknown vanished from the output
    with pytest.raises(FairnessAssertionError):
        assert_unknown_retained({0.0: base, 1.0: boosted}, k=2)


def test_assert_unknown_retained_detects_a_penalized_unknown() -> None:
    unknown = make_artist("mystery", gender=Gender.UNKNOWN)
    base = [_rec(unknown, 0.5)]
    penalized = [_rec(unknown, 0.3)]  # score lowered by the (mis-implemented) lens
    with pytest.raises(FairnessAssertionError):
        assert_unknown_retained({0.0: base, 1.0: penalized}, k=1)


def test_retention_at_k_detects_rank_loss_hidden_by_full_catalog_presence() -> None:
    unknown = make_artist("mystery", gender=Gender.UNKNOWN)
    women = [make_artist(f"w{i}", gender=Gender.WOMAN) for i in range(5)]
    base = [_rec(women[0], 0.9), _rec(unknown, 0.8), *[_rec(a, 0.7) for a in women[1:]]]
    boosted = [*[_rec(a, 0.7, delta=0.5) for a in women], _rec(unknown, 0.8)]

    retention = unknown_retention({0.0: base, 1.0: boosted}, k=5)
    assert retention == {"0.00": 1.0, "1.00": 0.0}
    with pytest.raises(FairnessAssertionError, match="dropped from top-5"):
        assert_unknown_retained({0.0: base, 1.0: boosted}, k=5)


def test_retention_detects_worse_rank_even_when_unknown_stays_inside_top_k() -> None:
    unknown = make_artist("mystery", gender=Gender.UNKNOWN)
    woman = make_artist("w", gender=Gender.WOMAN)
    man = make_artist("m", gender=Gender.MAN)
    base = [_rec(unknown, 0.8), _rec(man, 0.7), _rec(woman, 0.6)]
    shifted = [_rec(woman, 0.6, delta=0.5), _rec(unknown, 0.8), _rec(man, 0.7)]

    with pytest.raises(FairnessAssertionError, match="lost rank"):
        assert_unknown_retained({0.0: base, 1.0: shifted}, k=3)


def test_retention_is_unmeasured_when_no_unknown_artists_present() -> None:
    """An absent segment has no retention, and must not be scored as a perfect one.

    This test previously asserted ``{"0.00": 1.0, "1.00": 1.0}`` -- it pinned the defect
    as intended behavior. A retention of 1.0 is the strongest possible form of the claim
    "no unknown-identity artist lost score or rank", and it was being emitted for a world
    where no unknown-identity artist existed to lose anything. `None` is the honest value,
    and `segment_base_count` is the denominator that lets a reader tell the two apart.
    """

    woman = make_artist("w", gender=Gender.WOMAN)
    worlds = {0.0: [_rec(woman, 0.4)], 1.0: [_rec(woman, 0.4, 0.5)]}
    retention = unknown_retention(worlds, k=1)

    assert retention == {"0.00": None, "1.00": None}
    assert segment_base_count(worlds, k=1, segment=UNKNOWN) == 0


def test_retention_is_a_real_number_when_the_segment_is_actually_present() -> None:
    """The companion to the test above: a measured world still measures."""

    unknown = make_artist("u", gender=Gender.UNKNOWN)
    worlds = {0.0: [_rec(unknown, 0.4)], 1.0: [_rec(unknown, 0.4, 0.5)]}

    assert unknown_retention(worlds, k=1) == {"0.00": 1.0, "1.00": 1.0}
    assert segment_base_count(worlds, k=1, segment=UNKNOWN) == 1


def test_the_report_says_whether_each_retention_guarantee_was_measured() -> None:
    """The guarantee booleans mean "no violation seen"; these say whether anything was.

    Without this pairing, `other_retention_all_lenses: true` over an empty segment is
    indistinguishable from the same value over a segment that was really checked -- and
    the OTHER guarantee exists precisely because #68 found the lens shipping a harms note
    that nothing verified.
    """

    woman = make_artist("w", gender=Gender.WOMAN)
    unknown = make_artist("u", gender=Gender.UNKNOWN)
    worlds = {
        0.0: [_rec(woman, 0.5), _rec(unknown, 0.4)],
        1.0: [_rec(woman, 0.6), _rec(unknown, 0.4)],
    }
    guarantees = cast("dict[str, object]", exposure_report(worlds, k=2)["guarantees"])

    # The unknown segment is present, so its guarantee is a real measurement.
    assert guarantees["unknown_retention_measured"] is True
    assert guarantees["unknown_base_count"] == 1

    # No Gender.OTHER artist exists in this world, so its guarantee measured nothing --
    # and the report has to say so rather than reporting a perfect score.
    assert guarantees["other_retention_measured"] is False
    assert guarantees["other_base_count"] == 0
    assert guarantees["min_other_retention"] is None


def test_rank_shift_for_an_absent_segment_is_not_reported_as_no_movement() -> None:
    """0.0 would read as "measured, and it did not move"."""

    woman = make_artist("w", gender=Gender.WOMAN)
    base = [_rec(woman, 0.5)]
    shift = rank_shift_by_segment(base, [_rec(woman, 0.6)])

    assert shift[WOMAN] == 0.0, "a present segment still reports a real number"
    assert shift[UNKNOWN] is None
    assert shift[OTHER] is None


# -- rank shift (honest re-ordering, no score penalty) -----------------------
def test_rank_shift_moves_aligned_up_without_penalizing_unknown(profile, catalog, source) -> None:
    base = recommend(profile, catalog, source, k=len(catalog), lens_strength=0.0)
    boosted = recommend(profile, catalog, source, k=len(catalog), lens_strength=1.0)
    shift = rank_shift_by_segment(base, boosted)
    assert shift[WOMAN] <= 0.0  # sourced women move up (or stay), never down on average
    # And the score guarantee still holds even though positions changed:
    assert_unknown_retained({0.0: base, 1.0: boosted}, k=len(base))
    assert shift[UNKNOWN] == 0.0


# -- cross-tab ---------------------------------------------------------------
def test_crosstab_counts_the_whole_candidate_pool(profile, catalog, source) -> None:
    recs = recommend(profile, catalog, source, k=len(catalog), lens_strength=0.0)
    table = popularity_identity_crosstab(recs)
    total = sum(count for row in table.values() for count in row.values())
    assert total == len(recs)


# -- the full emitted report -------------------------------------------------
def test_exposure_report_has_expected_shape_and_guarantee(profile, catalog, source) -> None:
    report = exposure_report(_recs_by_lens(profile, catalog, source), k=5)
    assert set(report) >= {
        "k",
        "lens_strengths",
        "segments",
        "exposure_at_k",
        "unknown_retention",
        "mean_rank_shift",
        "popularity_identity_crosstab",
        "guarantees",
    }
    guarantees = report["guarantees"]
    assert guarantees["unknown_retention_all_lenses"] is True
    assert guarantees["min_unknown_retention"] == 1.0
    assert guarantees["unknown_downranked_count"] == 0
    # exposure emitted at the required lens strengths (0.0 / 0.5 / 1.0 among them):
    assert {"0.00", "0.50", "1.00"} <= set(report["exposure_at_k"])


def test_fairness_report_from_demo_world_satisfies_the_guarantee(
    demo_user, scrobbles, catalog, source
) -> None:
    report = fairness_report(demo_user, scrobbles, catalog, source, k=5)
    assert report["guarantees"]["unknown_retention_all_lenses"] is True
    # Every lens strength in the sweep is reported with 100% unknown retention.
    assert set(report["unknown_retention"].values()) == {1.0}
