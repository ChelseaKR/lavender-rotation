"""Bias audit §B: nonbinary representable end-to-end; female-fronted distinct."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest
from pipeline.identity import (
    IdentityEvidence,
    assert_permitted_only,
    resolve_composition,
    resolve_identity,
)
from pipeline.models import (
    FrontPerson,
    Gender,
    IdentityBasis,
    IdentityError,
    IdentityLabel,
    InferenceForbiddenError,
    Source,
    SourceKind,
    UnsourcedIdentityError,
)
from recommender.hybrid import recommend


def test_nonbinary_resolves_from_sourced_statement(nonbinary_label) -> None:
    assert nonbinary_label.gender is Gender.NONBINARY
    assert nonbinary_label.basis is IdentityBasis.SELF_IDENTIFIED
    assert nonbinary_label.sources


def test_nonbinary_survives_end_to_end_in_recommendations(profile, catalog, source) -> None:
    recs = recommend(profile, catalog, source, k=99, lens_strength=1.0)
    shamir = next(r for r in recs if r.artist.artist_id == "shamir")
    assert shamir.artist.identity.gender is Gender.NONBINARY
    assert shamir.rerank_delta > 0.0  # nonbinary is values-aligned and boosted
    assert "nonbinary" in shamir.explanation.summary.lower()


def test_wikidata_qids_map_respectfully() -> None:
    cases = {
        "Q6581072": Gender.WOMAN,  # female
        "Q1052281": Gender.WOMAN,  # trans woman IS a woman
        "Q6581097": Gender.MAN,
        "Q2449503": Gender.MAN,  # trans man IS a man
        "Q48270": Gender.NONBINARY,
        "Q1097630": Gender.OTHER,  # intersex represented, not flattened to unknown
    }
    for qid, expected in cases.items():
        label = resolve_identity(
            [IdentityEvidence(SourceKind.WIKIDATA_P21, qid, "wd://x", "2026-05-31")]
        )
        assert label.gender is expected, qid


@pytest.mark.parametrize(
    ("qid", "expected", "boosted"),
    [
        ("Q1052281", Gender.WOMAN, True),
        ("Q1097630", Gender.OTHER, False),
    ],
)
def test_trans_and_intersex_labels_survive_end_to_end(
    profile, catalog, source, qid: str, expected: Gender, boosted: bool
) -> None:
    label = resolve_identity(
        [IdentityEvidence(SourceKind.WIKIDATA_P21, qid, "wd://x", "2026-07-11")]
    )
    changed_catalog = dict(catalog)
    changed_catalog["mystery-act"] = replace(catalog["mystery-act"], identity=label)
    recs = recommend(profile, changed_catalog, source, k=99, lens_strength=1.0)
    result = next(rec for rec in recs if rec.artist.artist_id == "mystery-act")
    assert result.artist.identity.gender is expected
    assert result.explanation.identity_basis is IdentityBasis.SELF_IDENTIFIED
    assert (result.rerank_delta > 0) is boosted


def test_unrecognized_qid_stays_unknown() -> None:
    label = resolve_identity(
        [IdentityEvidence(SourceKind.WIKIDATA_P21, "Q999999999", "wd://x", "2026-05-31")]
    )
    assert label.gender is Gender.UNKNOWN


def test_female_fronted_is_distinct_from_member_gender() -> None:
    """The band has no personal gender; the member keeps her own sourced label."""
    member = resolve_identity(
        [IdentityEvidence(SourceKind.ARTIST_STATEMENT, "woman", "c", "2026-05-31")]
    )
    fronts = [FrontPerson("Singer", "lead vocals", member)]
    comp = resolve_composition(
        fronts,
        [IdentityEvidence(SourceKind.DISCOGS_LINEUP, "lineup", "c", "2026-05-31")],
    )
    assert comp is not None
    assert comp.female_fronted is True
    # The member's gender is still her own self-ID, not the band's.
    assert comp.members_fronting[0].identity.gender is Gender.WOMAN


def test_conflicting_sources_lower_confidence() -> None:
    label = resolve_identity(
        [
            IdentityEvidence(SourceKind.ARTIST_STATEMENT, "woman", "c1", "2026-05-31"),
            IdentityEvidence(SourceKind.MUSICBRAINZ_GENDER, "male", "c2", "2026-05-31"),
        ]
    )
    # Highest-priority source (artist statement) wins, but confidence is hedged.
    assert label.gender is Gender.WOMAN
    assert label.confidence is not None and label.confidence <= 0.5


# --- FIX-10: conflict surfacing + the correction ledger invariants ----------


def test_conflicting_sources_surface_as_a_conflict() -> None:
    """Disagreement is never silently resolved away — both claims are kept."""
    label = resolve_identity(
        [
            IdentityEvidence(SourceKind.WIKIDATA_P21, "Q6581072", "wd://x", "2026-05-31"),
            IdentityEvidence(SourceKind.MUSICBRAINZ_GENDER, "male", "mb://x", "2026-05-31"),
        ]
    )
    assert label.conflict is True
    assert len(label.conflicting_claims) == 2
    kinds = {src.kind for src in label.conflicting_claims}
    assert kinds == {SourceKind.WIKIDATA_P21, SourceKind.MUSICBRAINZ_GENDER}
    asserted = {src.detail for src in label.conflicting_claims}
    assert asserted == {"Q6581072", "male"}


def test_agreeing_sources_carry_no_conflict() -> None:
    label = resolve_identity(
        [
            IdentityEvidence(SourceKind.WIKIDATA_P21, "Q6581072", "wd://x", "2026-05-31"),
            IdentityEvidence(SourceKind.MUSICBRAINZ_GENDER, "female", "mb://x", "2026-05-31"),
        ]
    )
    assert label.conflict is False
    assert label.conflicting_claims == ()


def test_unsourced_gender_is_unconstructible() -> None:
    """A non-unknown gender with no source raises — identity is never inferred."""
    with pytest.raises(UnsourcedIdentityError):
        IdentityLabel(gender=Gender.WOMAN, basis=IdentityBasis.SELF_IDENTIFIED)


def test_uncited_correction_is_unconstructible() -> None:
    """A 'correction' with an empty citation is unconstructible, same invariant."""
    with pytest.raises(UnsourcedIdentityError):
        Source(kind=SourceKind.ARTIST_STATEMENT, citation="  ", retrieved_at="2026-05-31")


def test_conflict_true_requires_at_least_two_distinct_asserted_genders() -> None:
    single = Source(SourceKind.WIKIDATA_P21, "wd://x", "2026-05-31", "Q6581072")
    with pytest.raises(IdentityError):
        IdentityLabel(
            gender=Gender.WOMAN,
            basis=IdentityBasis.SELF_IDENTIFIED,
            sources=(single,),
            conflict=True,
            conflicting_claims=(single,),
        )


def test_conflicting_claims_without_conflict_flag_is_unconstructible() -> None:
    a = Source(SourceKind.WIKIDATA_P21, "wd://x", "2026-05-31", "Q6581072")
    b = Source(SourceKind.MUSICBRAINZ_GENDER, "mb://x", "2026-05-31", "male")
    with pytest.raises(IdentityError):
        IdentityLabel(
            gender=Gender.WOMAN,
            basis=IdentityBasis.SELF_IDENTIFIED,
            sources=(a,),
            conflict=False,
            conflicting_claims=(a, b),
        )


def test_unknown_gender_cannot_carry_a_conflict() -> None:
    with pytest.raises(IdentityError):
        IdentityLabel(conflict=True)


def test_conflicting_claims_must_be_individual_identity_sources() -> None:
    a = Source(SourceKind.WIKIDATA_P21, "wd://x", "2026-05-31", "Q6581072")
    lineup = Source(SourceKind.DISCOGS_LINEUP, "d://x", "2026-05-31", "lineup")
    with pytest.raises(InferenceForbiddenError):
        IdentityLabel(
            gender=Gender.WOMAN,
            basis=IdentityBasis.SELF_IDENTIFIED,
            sources=(a,),
            conflict=True,
            conflicting_claims=(a, lineup),
        )


def test_conflict_requires_distinct_asserted_values_not_just_two_sources() -> None:
    """Two sources that happen to agree isn't a conflict, even flagged True."""
    a = Source(SourceKind.WIKIDATA_P21, "wd://x", "2026-05-31", "Q6581072")
    b = Source(SourceKind.ARTIST_STATEMENT, "as://x", "2026-05-31", "Q6581072")
    with pytest.raises(IdentityError):
        IdentityLabel(
            gender=Gender.WOMAN,
            basis=IdentityBasis.SELF_IDENTIFIED,
            sources=(a,),
            conflict=True,
            conflicting_claims=(a, b),
        )


def test_local_correction_flag_requires_artist_statement_kind() -> None:
    with pytest.raises(InferenceForbiddenError):
        Source(
            kind=SourceKind.WIKIDATA_P21,
            citation="https://example.org/x",
            retrieved_at="2026-07-01",
            is_local_correction=True,
        )


# --- CQ-47 mutation-hardening: exact resolver semantics ---------------------
# The first mutation run (2026-07-17, `make mutation`) left 46 of 122 mutants
# in pipeline/identity.py alive despite 100% branch coverage — coverage proves
# lines ran, not that anything asserted their results. The killable survivors
# clustered in four places: source-priority order under conflict, the
# deterministic confidence arithmetic, the evidence-filter loop, and the
# defensive guards. The tests below pin those behaviors exactly. (The
# remainder are equivalent mutants: enum `is` vs `==`, unreachable dict
# defaults, sort-key transforms that preserve order, no-op rounding widths.)

_TS = "2026-05-31"


def _ev(kind: SourceKind, value: str, citation: str = "c") -> IdentityEvidence:
    return IdentityEvidence(kind, value, citation, _TS)


def test_wikidata_outranks_musicbrainz_on_conflict() -> None:
    """Priority is behavior, not decoration: WD beats MB when they disagree."""
    label = resolve_identity(
        [
            _ev(SourceKind.MUSICBRAINZ_GENDER, "male", "mb://x"),
            _ev(SourceKind.WIKIDATA_P21, "Q6581072", "wd://x"),
        ]
    )
    assert label.gender is Gender.WOMAN
    assert label.conflict is True
    # The sources tuple is priority-ordered, deterministically.
    assert [s.kind for s in label.sources] == [
        SourceKind.WIKIDATA_P21,
        SourceKind.MUSICBRAINZ_GENDER,
    ]


def test_artist_statement_outranks_all_on_three_way_conflict() -> None:
    """Self-statement wins over both third-party sources (R6)."""
    label = resolve_identity(
        [
            _ev(SourceKind.WIKIDATA_P21, "Q6581097", "wd://x"),
            _ev(SourceKind.MUSICBRAINZ_GENDER, "male", "mb://x"),
            _ev(SourceKind.ARTIST_STATEMENT, "nonbinary", "as://x"),
        ]
    )
    assert label.gender is Gender.NONBINARY
    assert [s.kind for s in label.sources] == [
        SourceKind.ARTIST_STATEMENT,
        SourceKind.WIKIDATA_P21,
        SourceKind.MUSICBRAINZ_GENDER,
    ]


@pytest.mark.parametrize(
    ("kind", "value", "expected_confidence"),
    [
        (SourceKind.ARTIST_STATEMENT, "woman", 0.95),
        (SourceKind.WIKIDATA_P21, "Q6581072", 0.80),
        (SourceKind.MUSICBRAINZ_GENDER, "female", 0.70),
    ],
)
def test_single_source_confidence_is_exact(
    kind: SourceKind, value: str, expected_confidence: float
) -> None:
    """The documented per-kind base confidences, to the digit."""
    label = resolve_identity([_ev(kind, value)])
    assert label.confidence == expected_confidence


def test_agreeing_corroboration_bonus_is_exact() -> None:
    """+0.05 per extra agreeing source on top of the best base, capped at 0.99."""
    two = resolve_identity(
        [
            _ev(SourceKind.WIKIDATA_P21, "Q6581072", "wd://x"),
            _ev(SourceKind.MUSICBRAINZ_GENDER, "female", "mb://x"),
        ]
    )
    assert two.confidence == 0.85  # best 0.80 + one agreeing source

    three = resolve_identity(
        [
            _ev(SourceKind.ARTIST_STATEMENT, "woman", "as://x"),
            _ev(SourceKind.WIKIDATA_P21, "Q6581072", "wd://x"),
            _ev(SourceKind.MUSICBRAINZ_GENDER, "female", "mb://x"),
        ]
    )
    assert three.confidence == 0.99  # 0.95 + 0.10 hits the sub-certainty cap

    four = resolve_identity(
        [
            _ev(SourceKind.WIKIDATA_P21, "Q6581072", "wd://x"),
            _ev(SourceKind.MUSICBRAINZ_GENDER, "female", "mb://1"),
            _ev(SourceKind.MUSICBRAINZ_GENDER, "female", "mb://2"),
            _ev(SourceKind.MUSICBRAINZ_GENDER, "female", "mb://3"),
        ]
    )
    assert four.confidence == 0.95  # best 0.80 + three agreeing sources


def test_conflict_confidence_is_exactly_half() -> None:
    """Conflict hedges to exactly 0.5 — not merely 'low'."""
    label = resolve_identity(
        [
            _ev(SourceKind.WIKIDATA_P21, "Q6581072", "wd://x"),
            _ev(SourceKind.MUSICBRAINZ_GENDER, "male", "mb://x"),
        ]
    )
    assert label.confidence == 0.5


def test_band_source_is_skipped_not_short_circuiting() -> None:
    """A leading band-composition source must not stop the evidence scan."""
    label = resolve_identity(
        [
            _ev(SourceKind.DISCOGS_LINEUP, "lineup", "dc://x"),
            _ev(SourceKind.MUSICBRAINZ_GENDER, "female", "mb://x"),
        ]
    )
    assert label.gender is Gender.WOMAN


def test_composition_requires_lineup_and_sources_independently() -> None:
    """Missing either leg (fronts, sourced lineup) means None — not a guess."""
    band_ev = _ev(SourceKind.DISCOGS_LINEUP, "lineup", "dc://x")
    front = FrontPerson("Singer", "lead vocals", IdentityLabel())
    assert resolve_composition([], [band_ev]) is None
    assert resolve_composition([front], []) is None


def test_identity_evidence_is_immutable() -> None:
    """Evidence is a frozen record — provenance cannot be edited in flight."""
    ev = _ev(SourceKind.ARTIST_STATEMENT, "woman")
    with pytest.raises(FrozenInstanceError):
        ev.value = "man"  # type: ignore[misc]


def test_guard_accepts_artist_statement_which_sits_in_both_source_sets() -> None:
    """ARTIST_STATEMENT belongs to BOTH permitted sets; the guard must honor
    the union (not a difference) of them."""
    assert_permitted_only(
        [
            _ev(SourceKind.ARTIST_STATEMENT, "woman", "as://x"),
            _ev(SourceKind.DISCOGS_LINEUP, "lineup", "dc://x"),
            _ev(SourceKind.WIKIDATA_P21, "Q6581072", "wd://x"),
        ]
    )
