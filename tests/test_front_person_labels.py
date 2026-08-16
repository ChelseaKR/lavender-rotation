"""#69 — a sourced front-person's gender is never widened on its way to display.

The defect these tests exist to keep out: a band whose only sourced front-person
was a nonbinary artist rendered, on every surface, as a *"female-fronted band
(sourced lineup), distinct from any member's gender"*. That is misgendering a
real person in published output, on the strength of the sourced value the code
then overrode — and the trailing clause denied the derivation that produced it.

These tests are written as **absence-of-harm** assertions: the forbidden strings
must not appear on any surface that carries the label, rather than the fixed
replacement wording appearing somewhere. A future rewording stays green; a
future re-flattening does not.

Everything here is synthetic. No real artist is involved.
"""

from __future__ import annotations

import pytest
from app.render import render_cards_html
from pipeline.identity import IdentityEvidence, resolve_composition, resolve_identity
from pipeline.models import (
    VALUES_ALIGNED_GENDERS,
    Artist,
    BandComposition,
    FrontPerson,
    Gender,
    IdentityBasis,
    SourceKind,
)
from recommender.exposure import (
    FEMALE_FRONTED,
    NONBINARY_FRONTED,
    SEGMENTS,
    UNKNOWN,
    identity_segment,
)
from recommender.hybrid import recommend
from recommender.lens import VALUES_LENS, LensSpec
from recommender.rerank import is_unknown_artist
from recommender.why import artist_identity_phrase, why_this_artist

_TS = "2026-05-31"

#: Words that would mean a nonbinary front-person had been described as a woman.
#: "female-fronted" is included on its own because it is the exact defect string.
FEMALE_CODED = ("female", "woman", "women")

#: Raw values a permitted source can assert, per gender, for the front-person.
_ASSERTED: dict[Gender, str] = {
    Gender.WOMAN: "woman",
    Gender.NONBINARY: "nonbinary",
    Gender.MAN: "man",
    Gender.OTHER: "intersex",
}


def _band(artist_id: str, *front_genders: Gender) -> Artist:
    """A fully synthetic band fronted by people with the given *sourced* genders."""
    fronts = [
        FrontPerson(
            name=f"Front {i}",
            role="lead vocals",
            identity=resolve_identity(
                [
                    IdentityEvidence(
                        SourceKind.ARTIST_STATEMENT,
                        _ASSERTED[gender],
                        f"https://example.org/{artist_id}-front-{i}",
                        _TS,
                    )
                ]
            ),
        )
        for i, gender in enumerate(front_genders)
    ]
    composition = resolve_composition(
        fronts,
        [
            IdentityEvidence(
                SourceKind.DISCOGS_LINEUP,
                "lineup",
                f"https://www.discogs.com/artist/{artist_id}",
                _TS,
            )
        ],
    )
    assert composition is not None
    return Artist(
        artist_id=artist_id,
        name=artist_id.replace("-", " ").title(),
        tags=("indie rock",),
        composition=composition,
    )


def _card_html(page: str, artist_id: str) -> str:
    """The one `<article>` for ``artist_id``, so page-level copy is out of scope."""
    opening = f'<article class="card" aria-labelledby="h-{artist_id}">'
    start = page.index(opening)
    end = page.index("</article>", start)
    return page[start:end]


def _surfaces(rec) -> dict[str, str]:
    """Every rendered surface that carries the identity label, keyed by name."""
    why = why_this_artist(rec)
    page = render_cards_html([rec], lens_strength=1.0, username="demo")
    return {
        "identity_statement": why.identity_statement,
        "explanation_summary": rec.explanation.summary,
        "why.to_text": why.to_text(),
        "why.to_markdown": why.to_markdown(),
        "html_card": _card_html(page, rec.artist.artist_id),
    }


def _rec_for(profile, catalog, source, artist: Artist, lens: float = 1.0):
    changed = dict(catalog)
    changed[artist.artist_id] = artist
    for rec in recommend(profile, changed, source, k=99, lens_strength=lens):
        if rec.artist.artist_id == artist.artist_id:
            return rec
    raise AssertionError(f"{artist.artist_id} not in recommendations")


# --- The model: a sourced gender is not widened ------------------------------


def test_nonbinary_front_person_does_not_make_a_band_female_fronted() -> None:
    band = _band("nb-fronted", Gender.NONBINARY)
    assert band.sourced_front_genders == frozenset({Gender.NONBINARY})
    assert band.female_fronted is None, (
        "a band fronted only by a sourced nonbinary artist is not female-fronted"
    )
    assert band.composition is not None
    assert band.composition.female_fronted is None


def test_nonbinary_front_person_is_still_surfaced_by_the_lens() -> None:
    """Fixing the label must not cost the artist the boost the lens exists to give."""
    band = _band("nb-fronted", Gender.NONBINARY)
    assert band.values_aligned is True
    assert VALUES_LENS.aligned(band) is True
    assert VALUES_LENS.boost(band, 1.0) > 0.0


def test_female_fronted_does_not_move_when_the_lens_policy_moves() -> None:
    """`female_fronted` is a claim about the world, not a view of the lens's set.

    Before #69 it was computed from ``VALUES_ALIGNED_GENDERS``, so adding
    ``Gender.OTHER`` to the lens would have started calling an intersex-fronted
    band "female-fronted".
    """
    other_fronted = _band("other-fronted", Gender.OTHER)
    assert other_fronted.female_fronted is None
    wider = LensSpec(
        name="wider",
        aligned_genders=VALUES_ALIGNED_GENDERS | {Gender.OTHER},
        max_boost=0.5,
        rationale="synthetic",
        harms_note="synthetic",
    )
    assert wider.aligned(other_fronted) is True  # the lens may widen…
    assert other_fronted.female_fronted is None  # …the assertion must not follow


def test_unknown_front_person_asserts_nothing() -> None:
    """A front-person with no sourced gender is not evidence of any gender."""
    unsourced_front = FrontPerson("Front", "lead vocals")
    composition = BandComposition(
        members_fronting=(unsourced_front,),
        sources=resolve_composition(
            [unsourced_front],
            [
                IdentityEvidence(
                    SourceKind.DISCOGS_LINEUP, "lineup", "https://www.discogs.com/artist/x", _TS
                )
            ],
        ).sources,  # type: ignore[union-attr]
    )
    assert composition.sourced_front_genders == frozenset()
    assert composition.female_fronted is None
    assert composition.has_sourced_front_person_in(Gender) is False


# --- Rendering: no surface says "female" about a nonbinary front-person ------


def test_no_surface_renders_a_nonbinary_fronted_band_as_female_fronted(
    profile, catalog, source
) -> None:
    """THE regression. Every surface `why.py` feeds is checked, not just one."""
    rec = _rec_for(profile, catalog, source, _band("nb-fronted", Gender.NONBINARY))
    assert rec.explanation.identity_basis is IdentityBasis.BAND_COMPOSITION
    for name, rendered in _surfaces(rec).items():
        lowered = rendered.lower()
        assert "nonbinary" in lowered, f"{name} drops the sourced gender entirely"
        for token in FEMALE_CODED:
            assert token not in lowered, f"{name} renders a nonbinary front-person as {token!r}"


def test_nonbinary_fronted_band_keeps_its_lineup_citations(profile, catalog, source) -> None:
    """Naming the gender honestly must not cost the reader the provenance."""
    rec = _rec_for(profile, catalog, source, _band("nb-fronted", Gender.NONBINARY))
    why = why_this_artist(rec)
    assert why.provenance
    assert all(item.citation for item in why.provenance)
    assert why.inferred is False


@pytest.mark.parametrize(
    ("gender", "expected_fragment"),
    [
        (Gender.WOMAN, "a sourced woman"),
        (Gender.NONBINARY, "a sourced nonbinary artist"),
        (Gender.MAN, "a sourced man"),
        (Gender.OTHER, "outside this vocabulary"),
    ],
)
def test_each_sourced_front_gender_is_named_as_itself(
    gender: Gender, expected_fragment: str
) -> None:
    phrase = artist_identity_phrase(_band(f"{gender.value}-fronted", gender))
    assert expected_fragment in phrase
    assert "sourced lineup" in phrase
    # The clause that claimed the label was not about a member's gender, while
    # being derived from exactly that, must not come back.
    assert "distinct from any member" not in phrase


def test_a_band_fronted_by_a_woman_and_a_nonbinary_artist_names_both() -> None:
    phrase = artist_identity_phrase(_band("mixed", Gender.WOMAN, Gender.NONBINARY))
    assert "a sourced woman" in phrase
    assert "a sourced nonbinary artist" in phrase


def test_three_sourced_front_genders_are_all_named_in_a_stable_order() -> None:
    """No gender is dropped for brevity, and lineup order never changes the text."""
    one_order = artist_identity_phrase(_band("three", Gender.MAN, Gender.NONBINARY, Gender.WOMAN))
    other_order = artist_identity_phrase(_band("three", Gender.WOMAN, Gender.MAN, Gender.NONBINARY))
    assert one_order == other_order
    assert "fronted by a sourced woman, a sourced nonbinary artist, and a sourced man" in one_order


def test_repeated_front_gender_is_pluralised_not_repeated() -> None:
    phrase = artist_identity_phrase(_band("two-nb", Gender.NONBINARY, Gender.NONBINARY))
    assert "sourced nonbinary artists" in phrase
    assert "a sourced nonbinary artist," not in phrase


def test_an_unsourced_front_person_adds_nothing_to_the_phrase() -> None:
    """An unknown front-person is an absence, never evidence of anyone's gender."""
    band = _band("nb-plus-unknown", Gender.NONBINARY)
    assert band.composition is not None
    with_unknown = Artist(
        artist_id=band.artist_id,
        name=band.name,
        composition=BandComposition(
            members_fronting=(
                *band.composition.members_fronting,
                FrontPerson("Unsourced Front", "lead vocals"),
            ),
            sources=band.composition.sources,
        ),
    )
    assert with_unknown.sourced_front_genders == frozenset({Gender.NONBINARY})
    assert artist_identity_phrase(with_unknown) == artist_identity_phrase(band)


def test_a_band_with_no_sourced_front_gender_stays_first_class_unknown() -> None:
    band = Artist(artist_id="no-lineup", name="No Lineup")
    assert artist_identity_phrase(band) == "unknown — surfaced on musical similarity alone"


# --- The fairness report segments at the granularity the source asserted ------


def test_exposure_segments_a_nonbinary_fronted_band_as_nonbinary_fronted() -> None:
    assert identity_segment(_band("nb-fronted", Gender.NONBINARY)) == NONBINARY_FRONTED
    assert identity_segment(_band("w-fronted", Gender.WOMAN)) == FEMALE_FRONTED
    assert NONBINARY_FRONTED in SEGMENTS


def test_an_individual_sourced_gender_still_outranks_the_lineup() -> None:
    """A solo artist's own sourced gender is never overridden by lineup data."""
    band = _band("solo-with-lineup", Gender.WOMAN)
    solo = Artist(
        artist_id=band.artist_id,
        name=band.name,
        identity=resolve_identity(
            [IdentityEvidence(SourceKind.ARTIST_STATEMENT, "nonbinary", "https://e.org/s", _TS)]
        ),
        composition=band.composition,
    )
    assert identity_segment(solo) == "nonbinary"
    assert "nonbinary, self-identified" in artist_identity_phrase(solo)


@pytest.mark.parametrize(
    "fronts",
    [
        (),
        (Gender.WOMAN,),
        (Gender.NONBINARY,),
        (Gender.MAN,),
        (Gender.OTHER,),
        (Gender.WOMAN, Gender.NONBINARY),
        (Gender.MAN, Gender.OTHER),
    ],
)
def test_unknown_protection_and_unknown_segment_agree(fronts: tuple[Gender, ...]) -> None:
    """The re-rank's pinned set and the fairness report's ``unknown`` must not diverge.

    ``rerank.is_unknown_artist`` says who keeps their pure-taste slot;
    ``exposure.identity_segment`` says who the retention guarantee is checked
    for. If they disagree, an artist is either protected without being measured
    or measured without being protected.
    """
    artist = _band("agreement", *fronts) if fronts else Artist("agreement", "Agreement")
    assert is_unknown_artist(artist) == (identity_segment(artist) == UNKNOWN)
