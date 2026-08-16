"""The second identity axis and the queer lens (ADR 0011).

This is the most sensitive data the project holds, so the tests are weighted
toward the negative space — what must *not* be concluded — rather than toward
the happy path:

* `trans_self_identified` is tri-state and can never be `False`. "Not recorded
  as trans" is not "recorded as cis", and the model refuses to express it.
* An absent orientation is never heterosexuality, and an absent trans claim is
  never a claim of any kind.
* The two axes cannot contaminate each other: a P91 claim can never move a
  gender label, and a P21 claim can never move an orientation.
* The lens is gated on gender, so "queer women and nonbinary people" cannot
  silently widen into "everyone queer".

Every QID here was verified against live Wikidata on 2026-08-16 (see
`pipeline/identity.py`). Artist names are invented, for the reason
`tests/test_live_enrichment.py` gives.
"""

from __future__ import annotations

import pytest
from pipeline.enrich import parse_wikidata_p91
from pipeline.identity import IdentityEvidence, resolve_identity, resolve_queer_identity
from pipeline.models import (
    Artist,
    Gender,
    IdentityError,
    InferenceForbiddenError,
    Orientation,
    QueerIdentity,
    SourceKind,
    UnsourcedIdentityError,
)
from pipeline.serde import artist_from_dict, artist_to_dict
from recommender.lens import LENSES, QUEER_LENS, VALUES_LENS

RETRIEVED = "2026-08-16"
WIKI = "https://www.wikidata.org/wiki/Q1"


def ev(kind: SourceKind, value: str, citation: str = WIKI) -> IdentityEvidence:
    return IdentityEvidence(kind=kind, value=value, citation=citation, retrieved_at=RETRIEVED)


# --- the negative space -----------------------------------------------------


def test_trans_self_identified_can_never_be_false() -> None:
    """The model refuses to express "this person is not trans"."""
    with pytest.raises(IdentityError, match="tri-state"):
        QueerIdentity(trans_self_identified=False)


def test_no_evidence_means_unknown_on_both_halves() -> None:
    """Not heterosexual. Not cis. Just unsourced, which is almost everyone."""
    queer = resolve_queer_identity([])

    assert queer.orientation is Orientation.UNKNOWN
    assert queer.trans_self_identified is None
    assert queer.sources == ()
    assert queer.is_known is False


def test_an_unrecognised_statement_contributes_nothing() -> None:
    queer = resolve_queer_identity([ev(SourceKind.ARTIST_STATEMENT, "it's complicated")])
    assert queer.orientation is Orientation.UNKNOWN


def test_a_sourceless_orientation_cannot_be_constructed() -> None:
    with pytest.raises(UnsourcedIdentityError):
        QueerIdentity(orientation=Orientation.LESBIAN)


def test_a_trans_claim_without_a_source_cannot_be_constructed() -> None:
    with pytest.raises(UnsourcedIdentityError):
        QueerIdentity(trans_self_identified=True)


def test_a_lineup_source_cannot_establish_an_orientation() -> None:
    with pytest.raises(InferenceForbiddenError):
        QueerIdentity(
            orientation=Orientation.QUEER,
            orientation_sources=(
                ev(
                    SourceKind.DISCOGS_LINEUP, "lineup", "https://www.discogs.com/artist/1-X"
                ).as_source(),
            ),
        )


# --- the two axes cannot contaminate each other -----------------------------


def test_an_orientation_claim_never_moves_a_gender_label() -> None:
    label = resolve_identity([ev(SourceKind.WIKIDATA_P91, "Q6649")])  # lesbianism
    assert label.gender is Gender.UNKNOWN
    assert label.sources == ()


def test_a_gender_claim_never_moves_an_orientation() -> None:
    queer = resolve_queer_identity([ev(SourceKind.WIKIDATA_P21, "Q6581072")])  # female
    assert queer.orientation is Orientation.UNKNOWN


@pytest.mark.parametrize(
    ("qid", "expected"),
    [
        ("Q6636", Orientation.HOMOSEXUAL),
        ("Q6649", Orientation.LESBIAN),
        ("Q592", Orientation.GAY),
        ("Q43200", Orientation.BISEXUAL),
        ("Q271534", Orientation.PANSEXUAL),
        ("Q724351", Orientation.ASEXUAL),
        ("Q1035954", Orientation.HETEROSEXUAL),
        ("Q43455", Orientation.UNKNOWN),  # ethnology — the plausible wrong guess
        ("Q6581072", Orientation.UNKNOWN),  # a *gender* QID is not an orientation
    ],
)
def test_the_verified_p91_vocabulary(qid: str, expected: Orientation) -> None:
    assert resolve_queer_identity([ev(SourceKind.WIKIDATA_P91, qid)]).orientation is expected


def test_the_artists_own_words_outrank_a_registry() -> None:
    queer = resolve_queer_identity(
        [
            ev(SourceKind.WIKIDATA_P91, "Q1035954"),  # a registry says heterosexual
            ev(SourceKind.ARTIST_STATEMENT, "queer", "https://example.org/interview"),
        ]
    )
    assert queer.orientation is Orientation.QUEER
    assert len(queer.orientation_sources) == 2, "the disagreeing claim is kept, not dropped"


# --- trans self-identification is read, not collected -----------------------


@pytest.mark.parametrize("value", ["Q1052281", "Q2449503", "Q189125", "trans woman", "Transgender"])
def test_a_trans_self_identification_already_in_the_cache_is_read(value: str) -> None:
    """No new fetch: these are values a gender source already asserted."""
    queer = resolve_queer_identity([ev(SourceKind.WIKIDATA_P21, value)])
    assert queer.trans_self_identified is True
    assert queer.trans_sources[0].detail == value


def test_a_trans_woman_is_still_simply_a_woman() -> None:
    """The amendment did not put a cis/trans distinction into `Gender` (ADR 0011)."""
    evidence = [ev(SourceKind.WIKIDATA_P21, "Q1052281")]

    assert resolve_identity(evidence).gender is Gender.WOMAN
    assert str(resolve_identity(evidence).gender) == "woman"


def test_a_plain_gender_claim_asserts_nothing_about_being_trans() -> None:
    queer = resolve_queer_identity([ev(SourceKind.MUSICBRAINZ_GENDER, "female")])
    assert queer.trans_self_identified is None


# --- the lens ---------------------------------------------------------------


def woman(orientation: Orientation = Orientation.UNKNOWN, *, trans: bool = False) -> Artist:
    evidence = [ev(SourceKind.WIKIDATA_P21, "Q1052281" if trans else "Q6581072")]
    if orientation is not Orientation.UNKNOWN:
        evidence.append(ev(SourceKind.ARTIST_STATEMENT, orientation.value, "https://example.org/x"))
    return Artist(
        artist_id="a",
        name="An Artist",
        identity=resolve_identity(evidence),
        queer=resolve_queer_identity(evidence),
    )


@pytest.mark.parametrize(
    ("orientation", "aligned"),
    [
        (Orientation.LESBIAN, True),
        (Orientation.BISEXUAL, True),
        (Orientation.PANSEXUAL, True),
        (Orientation.QUEER, True),
        (Orientation.ASEXUAL, False),  # recorded, deliberately not boosted
        (Orientation.HETEROSEXUAL, False),
        (Orientation.UNKNOWN, False),
    ],
)
def test_the_queer_lens_boosts_sourced_queer_women(orientation: Orientation, aligned: bool) -> None:
    assert QUEER_LENS.aligned(woman(orientation)) is aligned


def test_a_sourced_trans_woman_is_aligned_without_an_orientation_claim() -> None:
    assert QUEER_LENS.aligned(woman(trans=True)) is True


def test_a_nonbinary_artist_aligns_on_gender_alone() -> None:
    """No second, rarer disclosure is demanded of the least-documented group."""
    evidence = [ev(SourceKind.WIKIDATA_P21, "Q48270")]
    artist = Artist(artist_id="n", name="N", identity=resolve_identity(evidence))

    assert artist.queer.is_known is False
    assert QUEER_LENS.aligned(artist) is True


def test_a_queer_man_is_out_of_scope_not_penalised() -> None:
    """The lens is 'queer women and nonbinary people'; scope is stated, not hidden."""
    evidence = [
        ev(SourceKind.WIKIDATA_P21, "Q6581097"),  # male
        ev(SourceKind.WIKIDATA_P91, "Q592"),  # gay
    ]
    artist = Artist(
        artist_id="m",
        name="M",
        identity=resolve_identity(evidence),
        queer=resolve_queer_identity(evidence),
    )

    assert artist.queer.orientation is Orientation.GAY, "still recorded faithfully"
    assert QUEER_LENS.aligned(artist) is False
    assert QUEER_LENS.boost(artist, 1.0) == 0.0, "no boost is not a penalty"


def test_an_unknown_gender_is_not_gated_into_the_lens() -> None:
    evidence = [ev(SourceKind.WIKIDATA_P91, "Q6649")]
    artist = Artist(
        artist_id="u",
        name="U",
        identity=resolve_identity(evidence),
        queer=resolve_queer_identity(evidence),
    )
    assert artist.identity.gender is Gender.UNKNOWN
    assert QUEER_LENS.aligned(artist) is False


def test_the_default_lens_is_unchanged_by_the_second_axis() -> None:
    """The existing manifest must not have silently widened."""
    assert VALUES_LENS.aligned_orientations == frozenset()
    assert VALUES_LENS.queer_gate_genders == frozenset()
    assert VALUES_LENS.include_trans_self_identified is False
    assert VALUES_LENS.aligned(woman(Orientation.LESBIAN)) is True, "a woman, as before"
    assert LENSES["women-nonbinary"] is VALUES_LENS


def test_the_lens_never_returns_a_negative_boost() -> None:
    for artist in (woman(Orientation.LESBIAN), woman(Orientation.HETEROSEXUAL), woman()):
        for strength in (0.0, 0.5, 1.0):
            assert 0.0 <= QUEER_LENS.boost(artist, strength) <= QUEER_LENS.max_boost


# --- plumbing ---------------------------------------------------------------


def test_the_second_axis_round_trips_through_the_cache() -> None:
    evidence = [ev(SourceKind.WIKIDATA_P21, "Q1052281"), ev(SourceKind.WIKIDATA_P91, "Q6649")]
    artist = Artist(
        artist_id="a",
        name="An Artist",
        identity=resolve_identity(evidence),
        queer=resolve_queer_identity(evidence),
    )
    assert artist_from_dict(artist_to_dict(artist)) == artist


def test_a_cache_row_written_before_the_amendment_still_loads() -> None:
    legacy = {"artist_id": "a", "name": "A", "tags": [], "identity": None}
    restored = artist_from_dict(legacy)

    assert restored.queer.orientation is Orientation.UNKNOWN
    assert restored.queer.trans_self_identified is None


def test_p91_is_parsed_from_the_entity_document() -> None:
    payload = {"claims": {"P91": [{"mainsnak": {"datavalue": {"value": {"id": "Q6649"}}}}]}}
    evidence = parse_wikidata_p91(payload, WIKI, RETRIEVED)

    assert evidence is not None
    assert evidence.kind is SourceKind.WIKIDATA_P91
    assert evidence.value == "Q6649"


def test_an_entity_with_no_p91_claim_yields_nothing() -> None:
    assert parse_wikidata_p91({"claims": {}}, WIKI, RETRIEVED) is None
