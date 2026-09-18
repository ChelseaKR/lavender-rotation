"""Model-level guardrail invariants (the type system enforces the hard rules)."""

from __future__ import annotations

import pytest
from pipeline.models import (
    UNKNOWN_IDENTITY,
    Artist,
    BandComposition,
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


def _src(kind: SourceKind = SourceKind.WIKIDATA_P21) -> Source:
    return Source(kind=kind, citation="cite://x", retrieved_at="2026-05-31")


def test_unknown_is_first_class_and_default() -> None:
    label = IdentityLabel()
    assert label.gender is Gender.UNKNOWN
    assert label.basis is IdentityBasis.UNKNOWN
    assert not label.is_known
    assert UNKNOWN_IDENTITY.gender is Gender.UNKNOWN
    assert Artist("a", "A").identity.gender is Gender.UNKNOWN


def test_non_unknown_gender_requires_a_source() -> None:
    with pytest.raises(UnsourcedIdentityError):
        IdentityLabel(gender=Gender.WOMAN, basis=IdentityBasis.SELF_IDENTIFIED)


def test_non_unknown_gender_requires_self_identified_basis() -> None:
    with pytest.raises(InferenceForbiddenError):
        IdentityLabel(gender=Gender.WOMAN, basis=IdentityBasis.BAND_COMPOSITION, sources=(_src(),))


def test_band_composition_source_cannot_set_individual_gender() -> None:
    with pytest.raises(InferenceForbiddenError):
        IdentityLabel(
            gender=Gender.WOMAN,
            basis=IdentityBasis.SELF_IDENTIFIED,
            sources=(_src(SourceKind.DISCOGS_LINEUP),),
        )


def test_unknown_must_not_carry_a_nonunknown_basis() -> None:
    with pytest.raises(IdentityError):
        IdentityLabel(gender=Gender.UNKNOWN, basis=IdentityBasis.SELF_IDENTIFIED)


def test_confidence_bounds_enforced() -> None:
    with pytest.raises(IdentityError):
        IdentityLabel(
            gender=Gender.WOMAN,
            basis=IdentityBasis.SELF_IDENTIFIED,
            sources=(_src(),),
            confidence=1.5,
        )


def test_source_requires_citation() -> None:
    with pytest.raises(UnsourcedIdentityError):
        Source(kind=SourceKind.WIKIDATA_P21, citation="   ", retrieved_at="2026-05-31")


def test_female_fronted_is_tristate_and_sourced() -> None:
    # No sources → unknown (None), never False.
    assert BandComposition().female_fronted is None
    woman = IdentityLabel(
        gender=Gender.WOMAN,
        basis=IdentityBasis.SELF_IDENTIFIED,
        sources=(Source(SourceKind.ARTIST_STATEMENT, "c", "2026-05-31"),),
    )
    comp = BandComposition(
        members_fronting=(FrontPerson("Lead", "vocals", woman),),
        sources=(Source(SourceKind.DISCOGS_LINEUP, "c", "2026-05-31"),),
    )
    assert comp.female_fronted is True
    # A front-person with unknown identity does not make it False — stays None.
    unknown_comp = BandComposition(
        members_fronting=(FrontPerson("Lead", "vocals", IdentityLabel()),),
        sources=(Source(SourceKind.DISCOGS_LINEUP, "c", "2026-05-31"),),
    )
    assert unknown_comp.female_fronted is None


def test_composition_rejects_non_composition_source() -> None:
    with pytest.raises(InferenceForbiddenError):
        BandComposition(sources=(Source(SourceKind.WIKIDATA_P21, "c", "2026-05-31"),))


def test_values_aligned_unknown_is_false_but_not_penalized_here() -> None:
    # values_aligned is False for unknown; the *non-penalty* is the re-rank's job.
    assert Artist("u", "U").values_aligned is False
