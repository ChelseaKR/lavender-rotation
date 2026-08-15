"""#68 — the lens's published harms note and the ranking must agree.

`VALUES_LENS.harms_note` is rendered to the reader under the lens caption
(`app/dashboard.py`). It used to promise that an artist sourced as
`Gender.OTHER` was "never down-ranked, never dropped, never treated worse than
an unknown-identity artist". The re-rank pinned only *unknown* slots, so a
sourced `Gender.OTHER` artist could be pushed below a **lower-scoring** unknown
one — the artist who told the project who they are getting less protection than
the artist who had not.

These tests are absence-of-harm assertions on *emitted output*: no artist with a
sourced identity may end up ranked below a lower-scoring unknown artist, and no
artist of any identity may lose score. They are deliberately not assertions
about the boost function — that is what the previous test in this area asserted,
and it stayed green throughout the defect.

Everything here is synthetic. No real artist is involved.
"""

from __future__ import annotations

import pytest
from pipeline.models import Explanation, Gender, IdentityBasis, Recommendation, Signal
from recommender.exposure import (
    FairnessAssertionError,
    assert_no_score_reduced,
    assert_other_retained,
    assert_unknown_retained,
    exposure_report,
    identity_segment,
    other_retention,
)
from recommender.lens import VALUES_LENS
from recommender.rerank import RANK_PROTECTED_GENDERS, is_rank_protected, rerank

from .conftest import make_artist

LENS_SWEEP: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)


def _rec(artist, base_score: float) -> Recommendation:
    return Recommendation(
        artist=artist,
        base_score=base_score,
        rerank_delta=0.0,
        explanation=Explanation(
            signals=(Signal("content", "shared tags: synthetic", 1.0),),
            identity_basis=IdentityBasis.UNKNOWN
            if artist.identity.gender is Gender.UNKNOWN
            else IdentityBasis.SELF_IDENTIFIED,
            identity_sources=artist.identity.sources,
            summary="synthetic",
        ),
    )


def _issue_68_world() -> list[Recommendation]:
    """The exact four synthetic recommendations from the issue's table."""
    return [
        _rec(make_artist("a_woman", gender=Gender.WOMAN), 0.60),
        _rec(make_artist("b_other", gender=Gender.OTHER), 0.90),
        _rec(make_artist("c_unknown"), 0.80),
        _rec(make_artist("d_man", gender=Gender.MAN), 0.70),
    ]


def _by_lens(recs: list[Recommendation]) -> dict[float, list[Recommendation]]:
    return {lens: rerank(list(recs), lens) for lens in LENS_SWEEP}


def _ranks(recs: list[Recommendation]) -> dict[str, int]:
    return {rec.artist.artist_id: rec.rank for rec in recs}


# --- The measured defect, as the issue reported it ---------------------------


def test_sourced_other_is_not_pushed_below_a_lower_scoring_unknown() -> None:
    """THE regression: #1 at 0.90 fell to #3, under a pinned unknown at 0.80."""
    by_lens = _by_lens(_issue_68_world())
    base = _ranks(by_lens[0.0])
    for lens in LENS_SWEEP:
        ranked = sorted(by_lens[lens], key=lambda r: r.rank)
        assert _ranks(ranked)["b_other"] <= base["b_other"], (
            f"sourced OTHER lost rank at lens {lens}"
        )
        by_id = {rec.artist.artist_id: rec for rec in ranked}
        other, unknown = by_id["b_other"], by_id["c_unknown"]
        assert not (other.score > unknown.score and other.rank > unknown.rank), (
            "a sourced-identity artist is ranked below a lower-scoring unknown artist"
        )


def test_the_lens_still_moves_an_aligned_artist_up() -> None:
    """The protection must not be bought by making the lens a no-op."""
    by_lens = _by_lens(_issue_68_world())
    assert _ranks(by_lens[0.0])["a_woman"] == 4
    assert _ranks(by_lens[1.0])["a_woman"] == 3
    assert _ranks(by_lens[1.0])["d_man"] == 4


def test_no_artist_of_any_identity_loses_score() -> None:
    """The half of the promise that holds for everyone, checked for everyone."""
    assert_no_score_reduced(_by_lens(_issue_68_world()))


@pytest.mark.parametrize("gender", list(Gender))
def test_no_gender_ever_loses_score_at_any_lens(gender: Gender) -> None:
    world = [
        _rec(make_artist("boosted", gender=Gender.WOMAN), 0.10),
        _rec(make_artist("subject", gender=gender), 0.90),
        _rec(make_artist("filler"), 0.50),
    ]
    assert_no_score_reduced(_by_lens(world))


# --- The guarantees are now checked, not merely claimed ----------------------


def test_assert_other_retained_passes_on_the_issue_world() -> None:
    by_lens = _by_lens(_issue_68_world())
    assert_other_retained(by_lens, k=4)
    assert_unknown_retained(by_lens, k=4)
    assert set(other_retention(by_lens, k=4).values()) == {1.0}


def test_assert_other_retained_would_catch_a_regression() -> None:
    """The check has teeth: hand it output where OTHER lost rank."""
    base = [
        _rec(make_artist("b_other", gender=Gender.OTHER), 0.90).with_rank(1),
        _rec(make_artist("a_woman", gender=Gender.WOMAN), 0.60).with_rank(2),
    ]
    regressed = [
        _rec(make_artist("a_woman", gender=Gender.WOMAN), 0.60).with_rank(1),
        _rec(make_artist("b_other", gender=Gender.OTHER), 0.90).with_rank(2),
    ]
    with pytest.raises(FairnessAssertionError, match="other"):
        assert_other_retained({0.0: base, 1.0: regressed}, k=2)


def test_assert_no_score_reduced_would_catch_a_regression() -> None:
    base = [_rec(make_artist("x", gender=Gender.MAN), 0.90)]
    penalised = [_rec(make_artist("x", gender=Gender.MAN), 0.40)]
    with pytest.raises(FairnessAssertionError, match="lost score"):
        assert_no_score_reduced({0.0: base, 1.0: penalised})


def test_exposure_report_publishes_the_other_guarantee() -> None:
    report = exposure_report(_by_lens(_issue_68_world()), k=4)
    guarantees = report["guarantees"]
    assert guarantees["other_retention_all_lenses"] is True
    assert guarantees["min_other_retention"] == 1.0
    assert guarantees["other_downranked_count"] == 0
    assert guarantees["no_score_reduced_any_artist"] is True
    assert "other_retention" in report


# --- The protected set is the one the note describes --------------------------


def test_rank_protected_set_is_unknown_and_other_only() -> None:
    """Sourced men are deliberately not protected — see the harms note.

    Pinning them too would leave aligned artists able to permute only among
    their own base slots, which is a lens that cannot change exposure at any
    strength. That trade is named in `harms_note`, not hidden here.
    """
    assert frozenset({Gender.UNKNOWN, Gender.OTHER}) == RANK_PROTECTED_GENDERS
    assert is_rank_protected(make_artist("m", gender=Gender.MAN)) is False
    assert is_rank_protected(make_artist("w", gender=Gender.WOMAN)) is False
    assert is_rank_protected(make_artist("nb", gender=Gender.NONBINARY)) is False


def test_a_boosted_other_artist_is_not_pinned() -> None:
    """Protection must never cost an artist a boost they earned.

    A sourced-OTHER artist fronting a values-aligned band is being paid by the
    lens, not displaced by it, so pinning them to their pure-taste slot would
    silently discard the boost.
    """
    from tests.test_front_person_labels import _band

    band = _band("aligned-band", Gender.WOMAN)
    from dataclasses import replace

    from pipeline.identity import IdentityEvidence, resolve_identity
    from pipeline.models import SourceKind

    other_sourced_band = replace(
        band,
        identity=resolve_identity(
            [
                IdentityEvidence(
                    SourceKind.ARTIST_STATEMENT, "intersex", "https://example.org/x", "2026-05-31"
                )
            ]
        ),
    )
    assert other_sourced_band.identity.gender is Gender.OTHER
    assert VALUES_LENS.aligned(other_sourced_band) is True
    assert is_rank_protected(other_sourced_band) is False


def test_segment_and_protection_disagree_only_where_the_note_says_so() -> None:
    """`unknown` and `other` are exactly the rank-protected segments."""
    for gender in Gender:
        artist = make_artist(f"g-{gender.value}", gender=gender)
        protected = is_rank_protected(artist)
        assert protected == (identity_segment(artist) in {"unknown", "other"})
