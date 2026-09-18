"""Metric: recommendations down-ranked solely for unknown identity = 0.

This is the project's central fairness guarantee, tested directly: turning the
values lens up must never lower an unknown (or sourced-not-aligned) artist's
score, drop it from the results, or move it below where pure taste placed it
relative to other non-boosted artists.
"""

from __future__ import annotations

import pytest
from pipeline.models import Gender
from recommender.feedback import Feedback
from recommender.hybrid import recommend
from recommender.rerank import rerank, values_boost_for_artist

from .conftest import make_artist


def _by_id(recs):
    return {r.artist.artist_id: r for r in recs}


def test_unknown_base_score_is_invariant_to_lens(profile, catalog, source) -> None:
    base = _by_id(recommend(profile, catalog, source, k=99, lens_strength=0.0))
    for strength in (0.25, 0.5, 0.75, 1.0):
        boosted = _by_id(recommend(profile, catalog, source, k=99, lens_strength=strength))
        for aid, rec in boosted.items():
            if not rec.artist.values_aligned:  # unknown or sourced-not-aligned
                assert rec.rerank_delta == 0.0
                assert rec.base_score == base[aid].base_score
                assert rec.score == base[aid].base_score


def test_unknown_artist_is_never_dropped(profile, catalog, source) -> None:
    for strength in (0.0, 1.0):
        ids = {
            r.artist.artist_id
            for r in recommend(profile, catalog, source, k=99, lens_strength=strength)
        }
        assert "mystery-act" in ids  # the unknown artist always survives


def test_lens_never_produces_a_negative_delta() -> None:
    for gender in Gender:
        artist = make_artist("x", gender=gender)
        for strength in (0.0, 0.5, 1.0):
            assert values_boost_for_artist(artist, strength) >= 0.0


def test_man_and_unknown_are_not_penalized_relative_to_each_other() -> None:
    from pipeline.models import Explanation, IdentityBasis, Recommendation, Signal

    def rec(aid, gender, base):
        artist = make_artist(aid, gender=gender)
        expl = Explanation(
            signals=(Signal("content", "tag", 1.0),),
            identity_basis=IdentityBasis.UNKNOWN
            if gender is Gender.UNKNOWN
            else IdentityBasis.SELF_IDENTIFIED,
            identity_sources=artist.identity.sources,
            summary="why",
        )
        return Recommendation(artist=artist, base_score=base, rerank_delta=0.0, explanation=expl)

    recs = [rec("man", Gender.MAN, 0.5), rec("unknown", Gender.UNKNOWN, 0.5)]
    out = {r.artist.artist_id: r for r in rerank(recs, lens_strength=1.0)}
    assert out["man"].rerank_delta == 0.0
    assert out["unknown"].rerank_delta == 0.0
    assert out["man"].score == 0.5 and out["unknown"].score == 0.5


def test_rerank_rejects_out_of_range_lens() -> None:
    with pytest.raises(ValueError):
        rerank([], lens_strength=1.5)


def test_feedback_does_not_reintroduce_an_identity_penalty(profile, catalog, source) -> None:
    """Thumbs feedback must stay artist-scoped end-to-end through ``recommend``.

    A thumbs-down on one sourced woman artist must not lower any other
    artist — not another sourced woman/nonbinary artist, not an unknown one —
    and the lens's own boost-only contract must still hold with feedback in
    the mix.
    """
    votes = [Feedback(username="demo", artist_id="snail-mail", vote=-1, ts=1)]
    base = _by_id(recommend(profile, catalog, source, k=99, lens_strength=0.5))
    fed = _by_id(recommend(profile, catalog, source, k=99, lens_strength=0.5, feedbacks=votes))

    assert fed["snail-mail"].score < base["snail-mail"].score  # the voted artist moves down
    for aid, rec in fed.items():
        if aid != "snail-mail":
            assert rec.score == base[aid].score  # nothing else is touched

    for rec in fed.values():  # the lens's boost-only contract survives feedback
        assert rec.rerank_delta >= 0.0

    assert "mystery-act" in fed  # unknown still surfaces
    assert fed["mystery-act"].rerank_delta == 0.0
