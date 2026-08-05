"""The identity resolver: sourced-only, unknown-by-default, **no inference path**.

This module turns *permitted-source evidence* into an :class:`IdentityLabel`. It
deliberately offers no way to derive gender from a name, a voice, an image, or a
genre:

* :func:`resolve_identity` accepts only :class:`IdentityEvidence`, whose ``kind``
  must be a member of :data:`~pipeline.models.PERMITTED_SOURCES`.
* Evidence carrying a non-permitted source kind is rejected at construction
  (:class:`~pipeline.models.Source`), so nothing inferred can even reach here.
* The default return is :data:`~pipeline.models.UNKNOWN_IDENTITY`.

The companion guardrail test (``tests/test_no_inference.py``) statically asserts
that no forbidden basis exists and that this resolver's signature exposes no
name/voice/image/genre input.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from pipeline.models import (
    BAND_COMPOSITION_SOURCES,
    INDIVIDUAL_IDENTITY_SOURCES,
    BandComposition,
    FrontPerson,
    Gender,
    IdentityBasis,
    IdentityLabel,
    InferenceForbiddenError,
    Source,
    SourceKind,
)

# --- Controlled vocabulary --------------------------------------------------
# Maps the *raw values a permitted source asserts* onto our self-ID vocabulary.
# Trans women are women; trans men are men. Values we cannot responsibly map
# (e.g. MusicBrainz "Not applicable", an unknown QID) are absent here and thus
# contribute no gender — leaving the label UNKNOWN. This is a *normalisation*
# table for sourced claims, never an inference rule.
_FREEFORM_VOCAB: dict[str, Gender] = {
    "woman": Gender.WOMAN,
    "female": Gender.WOMAN,
    "trans woman": Gender.WOMAN,
    "transgender female": Gender.WOMAN,
    "man": Gender.MAN,
    "male": Gender.MAN,
    "trans man": Gender.MAN,
    "transgender male": Gender.MAN,
    "nonbinary": Gender.NONBINARY,
    "non-binary": Gender.NONBINARY,
    "genderqueer": Gender.NONBINARY,
    "genderfluid": Gender.NONBINARY,
    "agender": Gender.NONBINARY,
    "third gender": Gender.NONBINARY,
    "other": Gender.OTHER,
    "intersex": Gender.OTHER,
}
# Wikidata P21 ("sex or gender") item ids.
_WIKIDATA_QID_VOCAB: dict[str, Gender] = {
    "Q6581072": Gender.WOMAN,  # female
    "Q1052281": Gender.WOMAN,  # trans woman
    "Q6581097": Gender.MAN,  # male
    "Q2449503": Gender.MAN,  # trans man
    "Q48270": Gender.NONBINARY,  # non-binary
    "Q48279": Gender.NONBINARY,  # third gender
    "Q1097630": Gender.OTHER,  # intersex
}

# Trust priority when sources are present (higher wins on disagreement).
_SOURCE_PRIORITY: dict[SourceKind, int] = {
    SourceKind.ARTIST_STATEMENT: 3,
    SourceKind.WIKIDATA_P21: 2,
    SourceKind.MUSICBRAINZ_GENDER: 1,
}
# Base confidence contributed by a single source of each kind.
_SOURCE_BASE_CONFIDENCE: dict[SourceKind, float] = {
    SourceKind.ARTIST_STATEMENT: 0.95,
    SourceKind.WIKIDATA_P21: 0.80,
    SourceKind.MUSICBRAINZ_GENDER: 0.70,
}


#: A MusicBrainz artist URL resolves by MBID, which is a UUID; a Wikidata one
#: resolves by Q-number. Anything else is a label, not a locator.
_MUSICBRAINZ_ARTIST = re.compile(
    r"^https://musicbrainz\.org/artist/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_WIKIDATA_ENTITY = re.compile(r"^https://www\.wikidata\.org/wiki/Q[1-9][0-9]*$")


def citation_problem(kind: SourceKind, citation: str) -> Optional[str]:
    """Why ``citation`` cannot locate a record for ``kind``, or ``None`` if it can.

    Deliberately *not* enforced in ``IdentityEvidence.__post_init__``: evidence is
    a plain value object built throughout the suite (including property tests that
    generate arbitrary citations), and a constructor invariant would impose a
    contract the rest of the codebase was not written against. It is a gate the
    shipped fixture is held to instead (``tests/test_demo_citations.py``), which
    is where the failure actually was.

    Only the two sources with a machine-checkable identifier scheme are checked.
    An artist statement or a Discogs lineup is cited by whatever URL carries it,
    and inventing a pattern for those would reject honest citations.
    """
    text = (citation or "").strip()
    if not text:
        return "is empty"
    if kind is SourceKind.MUSICBRAINZ_GENDER and not _MUSICBRAINZ_ARTIST.match(text):
        return "is not a MusicBrainz artist URL ending in an MBID (UUID)"
    if kind is SourceKind.WIKIDATA_P21 and not _WIKIDATA_ENTITY.match(text):
        return "is not a Wikidata entity URL ending in a Q-number"
    return None


@dataclass(frozen=True)
class IdentityEvidence:
    """A single piece of sourced evidence handed to the resolver.

    ``kind`` must be permitted; ``value`` is the raw claim from that source
    (e.g. ``"female"``, ``"Q6581072"``). Note there is no ``name``, ``image``,
    ``audio``, or ``genre`` field — by construction the resolver cannot see them.
    """

    kind: SourceKind
    value: str
    citation: str
    retrieved_at: str
    #: True for a locally-entered correction (FIX-10 corrections ledger),
    #: threaded through to :class:`~pipeline.models.Source` so it can be
    #: surfaced distinctly ("local correction") in provenance displays.
    is_local_correction: bool = False

    def as_source(self) -> Source:
        return Source(
            kind=self.kind,
            citation=self.citation,
            retrieved_at=self.retrieved_at,
            detail=self.value,
            is_local_correction=self.is_local_correction,
        )


def _map_value(kind: SourceKind, value: str) -> Optional[Gender]:
    """Normalise one sourced claim to the controlled vocabulary, or ``None``."""
    raw = value.strip()
    if kind is SourceKind.WIKIDATA_P21:
        return _WIKIDATA_QID_VOCAB.get(raw)
    return _FREEFORM_VOCAB.get(raw.lower())


def resolve_identity(evidence: Sequence[IdentityEvidence]) -> IdentityLabel:
    """Resolve an individual's identity from permitted evidence only.

    Returns :data:`~pipeline.models.UNKNOWN_IDENTITY` when no permitted evidence
    yields a mappable gender. Never raises on unknown input — unknown is a normal,
    first-class answer.
    """
    # Keep only individual-identity sources that map to a known gender. A
    # band-composition source contributes nothing to a *personal* gender claim.
    mapped: list[tuple[IdentityEvidence, Gender]] = []
    for ev in evidence:
        if ev.kind not in INDIVIDUAL_IDENTITY_SOURCES:
            continue
        gender = _map_value(ev.kind, ev.value)
        if gender is not None:
            mapped.append((ev, gender))

    if not mapped:
        return IdentityLabel()  # UNKNOWN — first-class, no source needed

    # Pick the gender asserted by the highest-priority source. Deterministic:
    # ties break on (priority, source kind value, citation).
    mapped.sort(
        key=lambda pair: (
            -_SOURCE_PRIORITY.get(pair[0].kind, 0),
            pair[0].kind.value,
            pair[0].citation,
        )
    )
    chosen_gender = mapped[0][1]
    genders_present = {g for _, g in mapped}
    agreement = len(genders_present) == 1

    sources = tuple(ev.as_source() for ev, _ in mapped)
    confidence = _compute_confidence(mapped, agreement)

    return IdentityLabel(
        gender=chosen_gender,
        basis=IdentityBasis.SELF_IDENTIFIED,
        sources=sources,
        confidence=confidence,
        # Disagreement is surfaced, never hidden (FIX-10): when permitted
        # sources don't agree, the full set of disagreeing claims travels with
        # the label alongside the highest-priority `chosen_gender` above.
        conflict=not agreement,
        conflicting_claims=sources if not agreement else (),
    )


def _compute_confidence(
    mapped: Sequence[tuple[IdentityEvidence, Gender]], agreement: bool
) -> float:
    """Deterministic confidence from source quality and agreement."""
    best = max(_SOURCE_BASE_CONFIDENCE.get(ev.kind, 0.5) for ev, _ in mapped)
    if not agreement:
        # Conflicting sourced claims — we still report the highest-priority one,
        # but flag the uncertainty honestly.
        return round(min(best, 0.5), 3)
    # Agreeing corroboration nudges confidence up, capped below certainty.
    bonus = 0.05 * (len(mapped) - 1)
    return round(min(0.99, best + bonus), 3)


def resolve_composition(
    fronts: Sequence[FrontPerson], evidence: Sequence[IdentityEvidence]
) -> Optional[BandComposition]:
    """Build sourced band composition from lineup/role evidence.

    ``fronts`` are the sourced front-people (each with their own resolved,
    possibly-unknown identity). ``evidence`` must be band-composition sources.
    Returns ``None`` when there is no sourced lineup — composition, like
    identity, defaults to unknown rather than to a guess.
    """
    comp_sources = tuple(ev.as_source() for ev in evidence if ev.kind in BAND_COMPOSITION_SOURCES)
    if not comp_sources or not fronts:
        return None
    return BandComposition(members_fronting=tuple(fronts), sources=comp_sources)


def assert_permitted_only(evidence: Sequence[IdentityEvidence]) -> None:
    """Defensive guard: raise if any evidence carries a non-permitted kind.

    Evidence is normally rejected earlier (at :class:`Source` construction); this
    gives callers an explicit, early, intention-revealing check.
    """
    for ev in evidence:
        if ev.kind not in (INDIVIDUAL_IDENTITY_SOURCES | BAND_COMPOSITION_SOURCES):
            raise InferenceForbiddenError(f"{ev.kind} is not a permitted source")
