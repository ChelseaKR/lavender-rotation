"""``LensSpec`` — the values lens as a declared, inspectable object.

Mirrors the guardrail-test patterns in ``tests/test_no_inference.py`` and
``tests/test_unknown_first_class.py``: the aligned predicate reads *sourced*
fields only, never raises, and the boost is bounded and non-negative. Also
locks in the explicit, documented decision to exclude ``Gender.OTHER`` from
the default lens's aligned set (see ``recommender/lens.py``).
"""

from __future__ import annotations

import pytest
from pipeline.models import Gender
from recommender.lens import VALUES_LENS, LensSpec
from recommender.rerank import is_rank_protected

from .conftest import make_artist


def test_lens_spec_boost_bounded() -> None:
    """The boost never exceeds max_boost and is never negative, for any gender."""
    for gender in Gender:
        artist = make_artist(f"a-{gender.value}", gender=gender)
        for strength in (-1.0, 0.0, 0.25, 0.5, 0.75, 1.0, 2.0):
            boost = VALUES_LENS.boost(artist, strength)
            assert boost >= 0.0
            assert boost <= VALUES_LENS.max_boost


def test_lens_boost_scales_with_strength() -> None:
    woman = make_artist("w", gender=Gender.WOMAN)
    assert VALUES_LENS.boost(woman, 0.0) == 0.0
    assert VALUES_LENS.boost(woman, 0.5) == pytest.approx(VALUES_LENS.max_boost * 0.5)
    assert VALUES_LENS.boost(woman, 1.0) == VALUES_LENS.max_boost


def test_lens_other_excluded() -> None:
    """Locks in the documented decision: OTHER is not aligned, gets 0 boost."""
    other = make_artist("other-artist", gender=Gender.OTHER)
    assert VALUES_LENS.aligned(other) is False
    assert VALUES_LENS.boost(other, 1.0) == 0.0
    assert Gender.OTHER not in VALUES_LENS.aligned_genders


def test_lens_other_is_not_penalised_like_unknown() -> None:
    """OTHER, like UNKNOWN, is a re-rank non-event — in score *and* in position.

    This test used to assert only ``boost(other) == boost(unknown) == 0.0``,
    which is a statement about the boost function and proves nothing about the
    ranking. It passed for as long as the ranking pushed sourced-OTHER artists
    below lower-scoring unknown ones (#68). It now asserts the property its own
    name claims: both are rank-protected by the same rule.
    """
    other = make_artist("other-artist", gender=Gender.OTHER)
    unknown = make_artist("unknown-artist")
    assert VALUES_LENS.boost(other, 1.0) == VALUES_LENS.boost(unknown, 1.0) == 0.0
    assert is_rank_protected(other) is True
    assert is_rank_protected(unknown) is True


def test_harms_note_does_not_promise_what_the_ranking_does_not_do() -> None:
    """The published note must not re-acquire the claim #68 falsified.

    ``harms_note`` is rendered to the reader (``app/dashboard.py``). Sourced men
    *can* lose list position, so a blanket "never down-ranked" for anyone
    unaligned is a promise the re-rank cannot keep. Asserted as an absence, so a
    rewording stays green and a re-promise does not.
    """
    note = VALUES_LENS.harms_note.lower()
    assert "never down-ranked" not in note
    assert "never treated worse than an unknown-identity artist" not in note
    # …and it must still say the two things that *are* true and checked.
    assert "assert_no_score_reduced" in note
    assert "assert_other_retained" in note


def test_lens_woman_and_nonbinary_are_aligned() -> None:
    assert VALUES_LENS.aligned(make_artist("w", gender=Gender.WOMAN)) is True
    assert VALUES_LENS.aligned(make_artist("nb", gender=Gender.NONBINARY)) is True


def test_lens_man_is_not_aligned() -> None:
    assert VALUES_LENS.aligned(make_artist("m", gender=Gender.MAN)) is False


def test_lens_aligned_only_sourced_fields_unknown_returns_false_never_raises() -> None:
    """UNKNOWN identity: aligned() returns False and never raises."""
    unknown = make_artist("mystery")
    assert unknown.identity.is_known is False
    assert VALUES_LENS.aligned(unknown) is False  # must not raise


@pytest.mark.parametrize("gender", list(Gender))
def test_lens_aligned_never_raises_for_any_sourced_gender(gender: Gender) -> None:
    artist = make_artist(f"g-{gender.value}", gender=gender)
    # Must not raise for any gender, sourced or not.
    VALUES_LENS.aligned(artist)


def test_zero_identity_constants_reachable_through_lensspec() -> None:
    """Guards the excellence bar: identity constants live behind LensSpec, not loose."""
    assert isinstance(VALUES_LENS, LensSpec)
    assert VALUES_LENS.aligned_genders == frozenset({Gender.WOMAN, Gender.NONBINARY})
    assert VALUES_LENS.max_boost == pytest.approx(0.5)
    assert VALUES_LENS.name
    assert VALUES_LENS.rationale
    assert VALUES_LENS.harms_note
    # The re-export in recommender.rerank stays in lockstep with the manifest.
    from recommender.rerank import MAX_BOOST

    assert VALUES_LENS.max_boost == MAX_BOOST


def test_lensspec_is_frozen_and_immutable() -> None:
    with pytest.raises(Exception):  # noqa: B017 - dataclasses.FrozenInstanceError
        VALUES_LENS.max_boost = 1.0  # type: ignore[misc]
