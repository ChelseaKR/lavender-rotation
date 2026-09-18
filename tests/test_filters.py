"""The listener's opt-in output filter, and the guardrail it must not break.

A filter is the one mechanism in this system that can make an artist disappear,
which makes it the easiest place to lose "unknown is first-class". The obvious
implementation — keep only what the lens boosts — deletes every unknown artist,
because ``values_aligned`` is ``False`` for an absent claim exactly as it is for
a man's. These tests pin the difference.
"""

from __future__ import annotations

import pytest
from pipeline.models import (
    Artist,
    BandComposition,
    FrontPerson,
    Gender,
    IdentityBasis,
    IdentityLabel,
    Source,
    SourceKind,
)
from recommender.filters import is_sourced_man_only
from recommender.hybrid import recommend

from tests.conftest import make_artist


def sourced(gender: Gender) -> IdentityLabel:
    # UNKNOWN is not a sourced claim — the model refuses to attach a citation to
    # one, which is the invariant this whole module is careful around.
    if gender is Gender.UNKNOWN:
        return IdentityLabel()
    return IdentityLabel(
        gender=gender,
        basis=IdentityBasis.SELF_IDENTIFIED,
        sources=(
            Source(
                kind=SourceKind.ARTIST_STATEMENT,
                citation="https://example.org/statement",
                retrieved_at="2026-08-15",
                detail=gender.value,
            ),
        ),
    )


def band(*front_genders: Gender) -> Artist:
    return Artist(
        artist_id="band",
        name="The Band",
        composition=BandComposition(
            members_fronting=tuple(
                FrontPerson(name=f"Front {i}", role="lead vocals", identity=sourced(g))
                for i, g in enumerate(front_genders)
            ),
            sources=(
                Source(
                    kind=SourceKind.DISCOGS_LINEUP,
                    citation="https://www.discogs.com/artist/1-The-Band",
                    retrieved_at="2026-08-15",
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("gender", "filtered"),
    [
        (Gender.MAN, True),
        (Gender.WOMAN, False),
        (Gender.NONBINARY, False),
        (Gender.OTHER, False),
        (Gender.UNKNOWN, False),
    ],
)
def test_only_a_sourced_man_is_filtered(gender: Gender, filtered: bool) -> None:
    """`OTHER` is a sourced identity outside the lens's scope, not a man's."""
    assert is_sourced_man_only(make_artist("a", gender)) is filtered


def test_an_unknown_artist_is_never_filtered() -> None:
    """The guardrail. An absent claim is not a claim, and is never a man's."""
    assert is_sourced_man_only(Artist(artist_id="a", name="Nobody Sourced")) is False


@pytest.mark.parametrize(
    ("fronts", "filtered"),
    [
        ((Gender.MAN,), True),
        ((Gender.MAN, Gender.MAN), True),
        ((Gender.WOMAN, Gender.MAN), False),
        ((Gender.NONBINARY, Gender.MAN), False),
        ((Gender.WOMAN,), False),
    ],
)
def test_a_lineup_is_filtered_only_when_every_sourced_front_is_a_man(
    fronts: tuple[Gender, ...], filtered: bool
) -> None:
    """A band fronted by a sourced woman *and* a sourced man is not "a man"."""
    assert is_sourced_man_only(band(*fronts)) is filtered


def test_a_band_of_unknown_fronts_is_kept() -> None:
    act = band(Gender.UNKNOWN)
    assert act.sourced_front_genders == frozenset()
    assert is_sourced_man_only(act) is False


def test_the_filter_is_off_by_default() -> None:
    """Nothing existing changes: the eval and every prior caller see the old result."""
    profile, catalog, source = _world()
    assert {r.artist.artist_id for r in recommend(profile, catalog, source, k=10)} == {
        "man",
        "woman",
        "nobody",
    }


def test_the_filter_removes_sourced_men_and_keeps_unknown() -> None:
    profile, catalog, source = _world()
    picks = recommend(profile, catalog, source, k=10, hide_sourced_men=True)

    ids = [r.artist.artist_id for r in picks]
    assert "man" not in ids
    assert "nobody" in ids, "an unknown artist must survive the filter"
    assert ids == sorted(
        ids, key=lambda i: -next(r.score for r in picks if r.artist.artist_id == i)
    )
    assert [r.rank for r in picks] == list(range(1, len(picks) + 1)), "ranks are renumbered"


def test_the_filter_does_not_rewrite_the_counterfactual_rank() -> None:
    """`base_rank` still describes the real pure-taste ordering, filter or not."""
    profile, catalog, source = _world()
    unfiltered = {
        r.artist.artist_id: r.base_rank for r in recommend(profile, catalog, source, k=10)
    }
    filtered = {
        r.artist.artist_id: r.base_rank
        for r in recommend(profile, catalog, source, k=10, hide_sourced_men=True)
    }
    for artist_id, base_rank in filtered.items():
        assert base_rank == unfiltered[artist_id], artist_id


def _world() -> tuple[object, dict[str, Artist], object]:
    from pipeline.lastfm import FixtureLastfm
    from pipeline.models import ListeningProfile

    profile = ListeningProfile(
        username="listener",
        play_counts={"seed": 5.0},
        artist_names={"seed": "Seed"},
        tags={"seed": ("folk", "indie")},
    )
    catalog = {
        "man": Artist(artist_id="man", name="A Man", tags=("folk",), identity=sourced(Gender.MAN)),
        "woman": Artist(
            artist_id="woman", name="A Woman", tags=("folk",), identity=sourced(Gender.WOMAN)
        ),
        "nobody": Artist(artist_id="nobody", name="Unsourced", tags=("folk", "indie")),
    }
    source = FixtureLastfm({}, {}, {"seed": [("man", 0.9), ("woman", 0.8), ("nobody", 0.7)]})
    return profile, catalog, source
