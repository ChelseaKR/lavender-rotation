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


def test_an_unrecognized_statement_contributes_nothing() -> None:
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


def test_a_queer_man_is_out_of_scope_not_penalized() -> None:
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


# --- end to end, because the unit tests missed a lens that did nothing ------


def test_the_chosen_lens_actually_reaches_the_ranking() -> None:
    """Regression: `rerank()` recomputed every boost with the *default* lens.

    Every assertion above passed while `--lens-name queer` was a no-op, because
    they all called `LensSpec.aligned()` directly and never went through
    `recommend()`. On real data it showed up immediately — the "queer lens"
    surfaced women with no sourced queer claim at all.
    """
    from pipeline.lastfm import FixtureLastfm
    from pipeline.models import ListeningProfile
    from recommender.hybrid import recommend

    catalog = {
        "queer_woman": _artist("queer_woman", "Q6581072", orientation="Q6649"),
        "woman": _artist("woman", "Q6581072"),
    }
    profile = ListeningProfile(
        username="l",
        play_counts={"seed": 1.0},
        artist_names={"seed": "Seed"},
        tags={"seed": ("folk",)},
    )
    source = FixtureLastfm({}, {}, {"seed": [("queer_woman", 0.5), ("woman", 0.5)]})

    by_id = {
        r.artist.artist_id: r
        for r in recommend(profile, catalog, source, k=10, lens_strength=1.0, lens=QUEER_LENS)
    }

    assert by_id["queer_woman"].rerank_delta == QUEER_LENS.max_boost
    assert by_id["woman"].rerank_delta == 0.0, "a woman with no sourced queer claim is not boosted"


def test_the_default_lens_still_ranks_exactly_as_before() -> None:
    """The threading must not have changed the shipped lens's behavior."""
    from pipeline.lastfm import FixtureLastfm
    from pipeline.models import ListeningProfile
    from recommender.hybrid import recommend

    catalog = {"woman": _artist("woman", "Q6581072"), "man": _artist("man", "Q6581097")}
    profile = ListeningProfile(
        username="l", play_counts={"seed": 1.0}, artist_names={"seed": "S"}, tags={"seed": ("f",)}
    )
    source = FixtureLastfm({}, {}, {"seed": [("woman", 0.5), ("man", 0.5)]})

    by_id = {
        r.artist.artist_id: r for r in recommend(profile, catalog, source, k=10, lens_strength=1.0)
    }
    assert by_id["woman"].rerank_delta > 0.0
    assert by_id["man"].rerank_delta == 0.0


def _artist(artist_id: str, gender_qid: str, orientation: str | None = None) -> Artist:
    evidence = [ev(SourceKind.WIKIDATA_P21, gender_qid)]
    if orientation:
        evidence.append(ev(SourceKind.WIKIDATA_P91, orientation))
    return Artist(
        artist_id=artist_id,
        name=artist_id.title(),
        tags=("folk",),
        identity=resolve_identity(evidence),
        queer=resolve_queer_identity(evidence),
    )


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


# --- #92: the evidence behind a queer-lens boost must be visible -------------
#
# `resolve_queer_identity` computed the orientation and trans citations at
# ingest and *nothing downstream read them*. `build_explanation` filled
# `identity_sources` from the gender label or the band lineup and nothing else,
# `why_this_artist` built `provenance` from that alone, and every renderer
# consumed only that. So a woman boosted by `--lens-name queer` because a P91
# claim sourced her as a lesbian showed exactly what a default-lens run showed
# for the same woman: the boost signal, and her *gender* citation. The one
# citation that put her in this lens was the one a reader could not see.
#
# ADR 0011 §4 states the opposite as a decision: "the why-card says 'recorded in
# Wikidata' versus 'stated by the artist', with the raw asserted value shown
# either way. A reader can always see whether the artist said it." And the ADR's
# Consequences call sourced-with-a-citation "load-bearing rather than
# precautionary" — a citation computed and shown to nobody is not load-bearing.


def _queer_recommendation(artist: Artist):
    from pipeline.lastfm import FixtureLastfm
    from pipeline.models import ListeningProfile
    from recommender.hybrid import recommend
    from recommender.lens import QUEER_LENS

    profile = ListeningProfile(
        username="l",
        play_counts={"seed": 1.0},
        artist_names={"seed": "Seed"},
        tags={"seed": ("folk",)},
    )
    source = FixtureLastfm({}, {}, {"seed": [(artist.artist_id, 0.9)]})
    recs = recommend(
        profile,
        {artist.artist_id: artist},
        source,
        k=5,
        lens_strength=1.0,
        lens=QUEER_LENS,
    )
    assert recs, "the fixture artist must be recommended for the card to exist"
    return recs[0]


def _sourced_queer_woman() -> Artist:
    """A sourced lesbian, sourced trans woman. Invented, per the module docstring."""
    evidence = [
        ev(SourceKind.WIKIDATA_P21, "Q1052281", "https://www.wikidata.org/wiki/Q000000003"),
        ev(SourceKind.WIKIDATA_P91, "Q6649", "https://www.wikidata.org/wiki/Q000000003"),
    ]
    return Artist(
        artist_id="violet-meridian",
        name="Violet Meridian",
        tags=("folk",),
        identity=resolve_identity(evidence),
        queer=resolve_queer_identity(evidence),
    )


def test_the_why_card_shows_the_orientation_citation_that_earned_the_boost() -> None:
    from recommender.why import why_this_artist

    rec = _queer_recommendation(_sourced_queer_woman())
    assert rec.rerank_delta > 0.0, "the queer lens must actually be boosting this pick"

    why = why_this_artist(rec)
    kinds = {p.source_kind for p in why.queer_provenance}
    assert "wikidata-p91" in kinds, "the orientation citation is missing from the card"
    p91 = next(p for p in why.queer_provenance if p.source_kind == "wikidata-p91")
    # The *raw asserted value*, not the resolved label: ADR 0011 §4's exact ask.
    assert p91.asserted_value == "Q6649"
    assert p91.citation.startswith("https://www.wikidata.org/wiki/")
    assert p91.retrieved_at == RETRIEVED


def test_the_why_card_shows_the_trans_self_identification_citation() -> None:
    from recommender.why import why_this_artist

    why = why_this_artist(_queer_recommendation(_sourced_queer_woman()))
    p21 = [p for p in why.queer_provenance if p.source_kind == "wikidata-p21"]
    assert p21, "the trans self-identification citation is missing from the card"
    # Listed under the queer heading *as well as* the gender heading: one
    # document, two claims. Hiding the second reading is the defect.
    assert p21[0].asserted_value == "Q1052281"
    assert any(p.source_kind == "wikidata-p21" for p in why.provenance)


def test_an_orientation_citation_is_never_offered_as_a_gender_basis() -> None:
    """The two axes must stay separated in the *rendered* object too.

    `IdentityBasis.SELF_IDENTIFIED` on this card means "her gender is sourced".
    A P91 claim says nothing about anyone's gender, so it must not appear in the
    list the basis describes.
    """
    from recommender.why import why_this_artist

    why = why_this_artist(_queer_recommendation(_sourced_queer_woman()))
    assert "wikidata-p91" not in {p.source_kind for p in why.provenance}


def test_a_card_with_no_sourced_queer_claim_renders_no_empty_state() -> None:
    """Silence is the normal answer and must never be printed as a negative.

    "No orientation sources" on a card would turn "nobody sourced this" into a
    visible claim about the artist — the readable-as-"not queer" failure ADR
    0011's tri-state model exists to refuse.
    """
    from recommender.why import QUEER_SOURCES_HEADING, why_this_artist

    plain = Artist(
        artist_id="plain",
        name="Plain Artist",
        tags=("folk",),
        identity=resolve_identity([ev(SourceKind.WIKIDATA_P21, "Q6581072")]),
    )
    why = why_this_artist(_queer_recommendation(plain))
    assert why.queer_provenance == ()
    assert QUEER_SOURCES_HEADING not in why.to_text()
    assert QUEER_SOURCES_HEADING not in why.to_markdown()


def test_every_shared_surface_renders_the_orientation_citation() -> None:
    """The four renderers that consume `why_this_artist`, checked together.

    They each read the shared object rather than re-deriving the wording, so one
    missing field made all four silent at once. The static HTML render is the
    one that is committed and publicly browsable.
    """
    from app.render import render_cards_html
    from recommender.why import QUEER_SOURCES_HEADING, why_this_artist

    rec = _queer_recommendation(_sourced_queer_woman())
    why = why_this_artist(rec)

    assert QUEER_SOURCES_HEADING in why.to_text()
    assert "Q6649" in why.to_text()
    assert QUEER_SOURCES_HEADING in why.to_markdown()
    assert "Q6649" in why.to_markdown()

    html = render_cards_html([rec.with_rank(1)], lens_strength=1.0, username="demo")
    assert QUEER_SOURCES_HEADING in html
    assert "Q6649" in html
    # And the fix-at-source link that only exists now that P91 has an anchor.
    assert "#P91" in html
    assert "correct this wikidata-p91 claim upstream" in html


def test_the_static_render_stays_accessible_with_the_new_section() -> None:
    from app.a11y_check import check_html
    from app.render import render_cards_html

    rec = _queer_recommendation(_sourced_queer_woman())
    html = render_cards_html([rec.with_rank(1)], lens_strength=1.0, username="demo")
    assert check_html(html) == []
