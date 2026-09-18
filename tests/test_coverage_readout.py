"""R1: the per-run identity-coverage readout makes 'unknown is first-class' visible.

The readout is purely descriptive — it reports what the pipeline already computed
and must never reframe the (common, expected) unknown case as a failure, nor feed
any score. These tests pin both the arithmetic and the honest framing.
"""

from __future__ import annotations

from pipeline.models import (
    Explanation,
    Gender,
    IdentityBasis,
    Recommendation,
    Signal,
)
from recommender.coverage import IdentityCoverage, identity_coverage
from recommender.hybrid import recommend

from .conftest import make_artist


def _rec(artist, basis: IdentityBasis) -> Recommendation:
    expl = Explanation(
        signals=(Signal("content", "shared tags: indie", 1.0),),
        identity_basis=basis,
        identity_sources=artist.identity.sources,
        summary="why",
    )
    return Recommendation(artist=artist, base_score=0.5, rerank_delta=0.0, explanation=expl)


def test_counts_partition_total(profile, catalog, source) -> None:
    recs = recommend(profile, catalog, source, k=99, lens_strength=1.0)
    cov = identity_coverage(recs)
    assert cov.total == len(recs)
    # The three bases partition every pick exactly.
    assert cov.self_identified + cov.band_composition + cov.unknown == cov.total
    # Gender tally partitions the self-identified set exactly.
    assert cov.women + cov.nonbinary + cov.men + cov.other == cov.self_identified
    assert cov.sourced == cov.self_identified + cov.band_composition


def test_demo_run_surfaces_every_basis(profile, catalog, source) -> None:
    cov = identity_coverage(recommend(profile, catalog, source, k=99, lens_strength=1.0))
    # The demo world spans women, nonbinary, female-fronted, and unknown.
    assert cov.women >= 1
    assert cov.nonbinary >= 1
    assert cov.band_composition >= 1
    assert cov.unknown >= 1  # unknown is present and counted, never hidden


def test_summary_frames_unknown_respectfully(profile, catalog, source) -> None:
    line = identity_coverage(
        recommend(profile, catalog, source, k=99, lens_strength=0.5)
    ).summary_line()
    assert "surfaced on musical similarity alone" in line
    assert "never down-ranked" in line
    lowered = line.lower()
    for pejorative in ("failure", "missing", "gap", "incomplete", "unidentified"):
        assert pejorative not in lowered


def test_all_unknown_run_is_not_pathologized() -> None:
    recs = [_rec(make_artist(f"u{i}"), IdentityBasis.UNKNOWN) for i in range(4)]
    cov = identity_coverage(recs)
    assert cov.unknown == 4
    assert cov.sourced == 0
    assert cov.unknown_fraction == 1.0
    assert cov.sourced_fraction == 0.0
    # Even an all-unknown run reads as a normal, first-class outcome.
    assert "surfaced on musical similarity alone" in cov.summary_line()


def test_breakdown_always_includes_unknown_row() -> None:
    cov = identity_coverage(
        [_rec(make_artist("w", gender=Gender.WOMAN), IdentityBasis.SELF_IDENTIFIED)]
    )
    labels = [label for label, _ in cov.basis_breakdown()]
    assert any(label.startswith("Unknown") for label in labels)
    assert cov.women == 1


def test_other_sourced_gender_is_tallied_distinctly() -> None:
    # A sourced self-identification outside the common terms is its own bucket,
    # never collapsed into unknown.
    cov = identity_coverage(
        [_rec(make_artist("o", gender=Gender.OTHER), IdentityBasis.SELF_IDENTIFIED)]
    )
    assert cov.other == 1
    assert cov.self_identified == 1
    assert cov.unknown == 0


def test_empty_run_summary() -> None:
    cov = identity_coverage([])
    assert isinstance(cov, IdentityCoverage)
    assert cov.total == 0
    assert cov.summary_line() == "No picks yet."


def test_an_empty_run_reports_no_share_rather_than_a_zero_one() -> None:
    """This test previously asserted the defect.

    It read ``cov.sourced_fraction == 0.0 and cov.unknown_fraction == 0.0``, which is a run
    where none of the picks were sourced *and* none were unknown -- a distribution that cannot
    exist. What it was actually pinning is a share computed over nothing and published as a
    measurement, the same shape ``segment_retention`` had before #129.
    """
    cov = identity_coverage([])
    assert cov.fractions_measured is False
    assert cov.sourced_fraction is None
    assert cov.unknown_fraction is None
    document = cov.to_dict()
    assert document["sourced_fraction"] is None
    assert document["unknown_fraction"] is None
    assert document["fractions_measured"] is False
    # The denominator is published either way, so a reader can see why there is no share.
    assert document["total"] == 0


def test_a_measured_run_publishes_two_shares_that_sum_to_one() -> None:
    """The arithmetic that makes the empty case obvious once it is stated.

    Over any run with picks these two are a partition of it. The old empty-run values were
    ``0.0`` and ``0.0``, which sum to zero -- the tell that no measurement had happened.
    """
    recs = [
        _rec(make_artist("w", gender=Gender.WOMAN), IdentityBasis.SELF_IDENTIFIED),
        _rec(make_artist("u1"), IdentityBasis.UNKNOWN),
        _rec(make_artist("u2"), IdentityBasis.UNKNOWN),
    ]
    cov = identity_coverage(recs)
    assert cov.fractions_measured is True
    assert cov.sourced_fraction is not None and cov.unknown_fraction is not None
    assert cov.sourced_fraction + cov.unknown_fraction == 1.0
    assert cov.sourced_fraction == 1 / 3


def test_to_dict_is_consistent(profile, catalog, source) -> None:
    cov = identity_coverage(recommend(profile, catalog, source, k=99, lens_strength=0.0))
    d = cov.to_dict()
    assert d["total"] == cov.total
    assert d["sourced"] == cov.sourced
    assert d["unknown"] == cov.unknown
    fraction = d["sourced_fraction"]
    assert isinstance(fraction, float)
    assert 0.0 <= fraction <= 1.0
    assert d["fractions_measured"] is True
