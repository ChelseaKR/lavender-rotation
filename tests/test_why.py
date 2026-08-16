"""'Why this artist': the explanation is transparent, sourced, and never inferred."""

from __future__ import annotations

import pytest
from pipeline.models import Artist, Gender, IdentityBasis, IdentityLabel, Source, SourceKind
from recommender.hybrid import recommend
from recommender.why import (
    ProvenanceItem,
    WhyThisArtist,
    artist_identity_phrase,
    conflict_note,
    rank_shift_statement,
    why_this_artist,
)


def _rec_for(profile, catalog, source, artist_id, lens=0.5):
    for rec in recommend(profile, catalog, source, k=99, lens_strength=lens):
        if rec.artist.artist_id == artist_id:
            return rec
    raise AssertionError(f"{artist_id} not in recommendations")


def test_every_recommendation_yields_a_why(profile, catalog, source) -> None:
    for rec in recommend(profile, catalog, source, k=99, lens_strength=0.5):
        why = why_this_artist(rec)
        assert isinstance(why, WhyThisArtist)
        assert why.inferred is False  # the hard guarantee, surfaced in the output
        assert why.headline
        assert why.reasons
        assert why.identity_statement
        assert why.rank_shift


def test_sourced_woman_shows_provenance_not_inference(profile, catalog, source) -> None:
    rec = _rec_for(profile, catalog, source, "snail-mail")
    why = why_this_artist(rec)
    assert why.identity_basis is IdentityBasis.SELF_IDENTIFIED
    assert why.identity_is_known
    assert why.provenance, "a sourced identity must carry its citations"
    for item in why.provenance:
        assert isinstance(item, ProvenanceItem)
        assert item.citation
        assert item.asserted_value  # the *raw* claim, auditable, not just a label
        assert item.retrieved_at
    # The raw asserted value and a real citation reach the rendered text.
    text = why.to_text()
    assert "sourced, never inferred" in text.lower()
    assert any(item.citation in text for item in why.provenance)


def test_unknown_is_first_class_and_honest(profile, catalog, source) -> None:
    rec = _rec_for(profile, catalog, source, "mystery-act", lens=1.0)
    why = why_this_artist(rec)
    assert why.identity_basis is IdentityBasis.UNKNOWN
    assert not why.identity_is_known
    assert why.provenance == ()
    assert "unknown" in why.identity_statement.lower()
    assert "similarity" in why.identity_statement.lower()
    # No apology, no guess — and the markdown says sources are absent, not wrong.
    assert "surfaced on merit" in why.to_markdown().lower()


def test_band_lineup_is_composition_basis_and_names_the_sourced_genders(
    profile, catalog, source
) -> None:
    """A woman-fronted band names *women*, and says what it is silent about.

    The old wording ("female-fronted band ... distinct from any member's
    gender") was doubly wrong: it asserted a category the source had not
    asserted for every front-person, and the trailing clause denied the very
    derivation the label came from. #69.
    """
    rec = _rec_for(profile, catalog, source, "boygenius")
    why = why_this_artist(rec)
    assert why.identity_basis is IdentityBasis.BAND_COMPOSITION
    statement = why.identity_statement.lower()
    assert "sourced women" in statement  # boygenius: two sourced women fronting
    assert "sourced lineup" in statement
    assert "no gender is claimed for any other member" in statement
    # The clause that told the reader the label was not about a person, while
    # being derived entirely from people's sourced genders, is gone.
    assert "distinct from any member" not in statement
    assert why.provenance  # sourced lineup citation present


def test_markdown_and_text_round_trip_the_reasons(profile, catalog, source) -> None:
    rec = _rec_for(profile, catalog, source, "snail-mail")
    why = why_this_artist(rec)
    md = why.to_markdown()
    txt = why.to_text()
    assert why.artist_name in md and why.artist_name in txt
    for reason in why.reasons:
        # Reasons appear in both renderings (markdown bullet / text bullet).
        assert reason in md
        assert reason in txt
    assert why.rank_shift in md and why.rank_shift in txt


def test_rank_shift_statement_wording() -> None:
    assert rank_shift_statement(4, 9) == "the values lens moved this pick from #9 to #4"
    assert rank_shift_statement(3, 3) == "the values lens did not change this pick's position"
    assert rank_shift_statement(5, 0) == "the values lens did not change this pick's position"


def test_rank_shift_does_not_misattribute_boost_as_movement(profile, catalog, source) -> None:
    rec = _rec_for(profile, catalog, source, "boygenius", lens=1.0)
    assert rec.rerank_delta > 0.0
    assert rec.base_rank == rec.rank == 3
    assert why_this_artist(rec).rank_shift == (
        "the values lens did not change this pick's position"
    )


def test_rank_shift_unchanged_at_zero_lens(profile, catalog, source) -> None:
    for rec in recommend(profile, catalog, source, k=99, lens_strength=0.0):
        assert rec.rank == rec.base_rank
        assert why_this_artist(rec).rank_shift == (
            "the values lens did not change this pick's position"
        )


def test_unknown_identity_never_shows_lens_caused_improvement(profile, catalog, source) -> None:
    for rec in recommend(profile, catalog, source, k=99, lens_strength=1.0):
        if why_this_artist(rec).identity_basis is IdentityBasis.UNKNOWN:
            assert rec.rank >= rec.base_rank


def test_artist_identity_phrase_matches_statement(profile, catalog, source) -> None:
    rec = _rec_for(profile, catalog, source, "snail-mail")
    assert artist_identity_phrase(rec.artist) == why_this_artist(rec).identity_statement


def test_artist_identity_phrase_uses_qualitative_tier_not_percentage() -> None:
    from pipeline.models import Artist, Gender, IdentityBasis, IdentityLabel, Source, SourceKind

    label = IdentityLabel(
        gender=Gender.WOMAN,
        basis=IdentityBasis.SELF_IDENTIFIED,
        sources=(
            Source(
                kind=SourceKind.ARTIST_STATEMENT,
                citation="https://example.org/statement",
                retrieved_at="2026-05-31",
                detail="woman",
            ),
        ),
        confidence=0.9,
    )
    artist = Artist(artist_id="known-female", name="Known Female", identity=label)
    phrase = artist_identity_phrase(artist)
    assert "directly stated by the artist" in phrase
    assert "%" not in phrase


@pytest.mark.parametrize(
    ("source_kind", "misleading_confidence", "expected"),
    [
        (SourceKind.ARTIST_STATEMENT, 0.01, "directly stated by the artist"),
        (SourceKind.WIKIDATA_P21, 0.99, "recorded in Wikidata"),
        (SourceKind.MUSICBRAINZ_GENDER, 0.99, "editorial database entry"),
    ],
)
def test_identity_tier_comes_from_citation_not_numeric_confidence(
    source_kind: SourceKind, misleading_confidence: float, expected: str
) -> None:
    from pipeline.models import Artist, Gender, IdentityBasis, IdentityLabel, Source

    label = IdentityLabel(
        gender=Gender.WOMAN,
        basis=IdentityBasis.SELF_IDENTIFIED,
        sources=(Source(source_kind, "https://example.org/source", "2026-05-31", "woman"),),
        confidence=misleading_confidence,
    )
    phrase = artist_identity_phrase(Artist("known", "Known", identity=label))
    assert expected in phrase


def test_provenance_item_from_source_preserves_raw_value() -> None:
    from pipeline.models import Source, SourceKind

    src = Source(
        kind=SourceKind.MUSICBRAINZ_GENDER,
        citation="https://musicbrainz.org/artist/x",
        retrieved_at="2026-05-31",
        detail="female",
    )
    item = ProvenanceItem.from_source(src)
    assert item.asserted_value == "female"
    assert item.source_kind == "musicbrainz-gender"
    assert item.citation == src.citation


def test_to_text_handles_no_reasons_branch() -> None:
    why = WhyThisArtist(
        artist_name="Nobody",
        headline="appears in your discovery catalog",
        reasons=(),
        identity_statement="identity unknown — surfaced on musical similarity alone",
        identity_basis=IdentityBasis.UNKNOWN,
        provenance=(),
    )
    text = why.to_text()
    assert "Nobody" in text
    assert "none" in text.lower()
    assert "Why recommended" not in text  # no reasons section when empty
    md = why.to_markdown()
    assert "Nobody" in md
    assert "surfaced on merit" in md.lower()
    assert "Why recommended" not in md  # markdown also omits the empty section


@pytest.mark.parametrize("lens", [0.0, 1.0])
def test_why_stable_across_lens(profile, catalog, source, lens) -> None:
    rec = _rec_for(profile, catalog, source, "mystery-act", lens=lens)
    why = why_this_artist(rec)
    assert why.identity_basis is IdentityBasis.UNKNOWN


def _conflicted_artist() -> Artist:
    wikidata = Source(SourceKind.WIKIDATA_P21, "wd://x", "2026-05-31", "Q6581072")
    musicbrainz = Source(SourceKind.MUSICBRAINZ_GENDER, "mb://x", "2026-05-31", "male")
    label = IdentityLabel(
        gender=Gender.WOMAN,
        basis=IdentityBasis.SELF_IDENTIFIED,
        sources=(wikidata,),
        confidence=0.5,
        conflict=True,
        conflicting_claims=(wikidata, musicbrainz),
    )
    return Artist(artist_id="conflicted", name="Conflicted Artist", identity=label)


def test_conflict_note_names_every_disagreeing_source() -> None:
    note = conflict_note(_conflicted_artist())
    assert note.startswith("Sources disagree:")
    assert "Q6581072" in note and "male" in note and "2026-05-31" in note
    assert "wrong" not in note.lower()


def test_conflict_note_empty_when_sources_agree(profile, catalog, source) -> None:
    why = why_this_artist(_rec_for(profile, catalog, source, "snail-mail"))
    assert why.conflict_note == ""


def test_conflict_note_renders_in_text_and_markdown() -> None:
    artist = _conflicted_artist()
    why = WhyThisArtist(
        artist_name=artist.name,
        headline="in your discovery catalog",
        reasons=("collaborative: similar listeners",),
        identity_statement=artist_identity_phrase(artist),
        identity_basis=IdentityBasis.SELF_IDENTIFIED,
        provenance=tuple(ProvenanceItem.from_source(s) for s in artist.identity.sources),
        conflict_note=conflict_note(artist),
    )
    assert why.conflict_note in why.to_text()
    assert why.conflict_note in why.to_markdown()
