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
    ORIENTATION_SOURCES,
    BandComposition,
    FrontPerson,
    Gender,
    IdentityBasis,
    IdentityLabel,
    InferenceForbiddenError,
    Orientation,
    QueerIdentity,
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

# --- Orientation vocabulary (ADR 0011) --------------------------------------
# Wikidata P91 ("sexual orientation") item ids. Every QID below was verified
# against live Wikidata on 2026-08-16 by fetching its English label, because
# this repo has already shipped three wrong Wikidata entities once and a
# plausible-looking Q-number is not evidence of anything. (Q43455 — the obvious
# guess for "queer" — is *ethnology*.)
_ORIENTATION_QID_VOCAB: dict[str, Orientation] = {
    "Q6636": Orientation.HOMOSEXUAL,  # homosexuality
    "Q6649": Orientation.LESBIAN,  # lesbianism
    "Q592": Orientation.GAY,  # gay
    "Q43200": Orientation.BISEXUAL,  # bisexuality
    "Q271534": Orientation.PANSEXUAL,  # pansexuality
    "Q724351": Orientation.ASEXUAL,  # asexuality
    "Q23912283": Orientation.DEMISEXUAL,  # demisexuality
    "Q1035954": Orientation.HETEROSEXUAL,  # heterosexuality
}
# What an artist's own words map to. Anything absent contributes nothing —
# there is no fallback that turns an unrecognised phrase into an orientation.
_ORIENTATION_FREEFORM_VOCAB: dict[str, Orientation] = {
    "lesbian": Orientation.LESBIAN,
    "gay": Orientation.GAY,
    "homosexual": Orientation.HOMOSEXUAL,
    "bisexual": Orientation.BISEXUAL,
    "bi": Orientation.BISEXUAL,
    "pansexual": Orientation.PANSEXUAL,
    "pan": Orientation.PANSEXUAL,
    "queer": Orientation.QUEER,
    "asexual": Orientation.ASEXUAL,
    "ace": Orientation.ASEXUAL,
    "demisexual": Orientation.DEMISEXUAL,
    "straight": Orientation.HETEROSEXUAL,
    "heterosexual": Orientation.HETEROSEXUAL,
}

# --- Trans self-identification (ADR 0011) -----------------------------------
# Read from values a *gender* source already asserted and this module already
# stored for provenance — no new fetch, no new field, nothing newly asked of
# anyone. `Gender` still maps every one of these to WOMAN/MAN/NONBINARY and
# still draws no cis/trans distinction of its own.
_TRANS_ASSERTED_VALUES: frozenset[str] = frozenset(
    {
        "Q1052281",  # trans woman
        "Q2449503",  # trans man
        "Q189125",  # transgender
        "trans woman",
        "trans man",
        "transgender",
        "transgender female",
        "transgender male",
        "transfeminine",
        "transmasculine",
    }
)

# Trust priority when sources are present (higher wins on disagreement).
_SOURCE_PRIORITY: dict[SourceKind, int] = {
    SourceKind.ARTIST_STATEMENT: 3,
    SourceKind.WIKIDATA_P21: 2,
    # P91 sits where P21 does — below the artist's own words, and for the same
    # reason. It is admitted for coverage (ADR 0011) while being, more often
    # than P21 is, a biographer's characterisation rather than a self-statement,
    # which is why the why-card renders the two differently.
    SourceKind.WIKIDATA_P91: 2,
    SourceKind.MUSICBRAINZ_GENDER: 1,
}
# Base confidence contributed by a single source of each kind.
_SOURCE_BASE_CONFIDENCE: dict[SourceKind, float] = {
    SourceKind.ARTIST_STATEMENT: 0.95,
    SourceKind.WIKIDATA_P21: 0.80,
    SourceKind.MUSICBRAINZ_GENDER: 0.70,
}


#: A MusicBrainz artist URL resolves by MBID, which is a UUID; a Wikidata one
#: resolves by Q-number; a Discogs artist URL resolves by numeric
#: release-database id followed by a slug. Anything else is a label, not a
#: locator.
_MUSICBRAINZ_ARTIST = re.compile(
    r"^https://musicbrainz\.org/artist/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_WIKIDATA_ENTITY = re.compile(r"^https://www\.wikidata\.org/wiki/Q[1-9][0-9]*$")
_DISCOGS_ARTIST = re.compile(r"^https://www\.discogs\.com/artist/[1-9][0-9]*-\S+$")

#: Host-scoped locator shapes, applied to **any** source kind. The moment a
#: citation claims to point into one of these registries, it has to be an
#: address that registry can actually resolve. A citation that claims no
#: registry — an interview, a label bio, a Wikipedia section — stays free-form,
#: which is why this is keyed on host rather than on kind: the original
#: reasoning ("inventing a pattern for a lineup would reject honest citations")
#: only ever held for citations that are not registry addresses.
_REGISTRY_LOCATORS: tuple[tuple[str, str, str], ...] = (
    (
        "https://musicbrainz.org/artist/",
        "musicbrainz.org",
        "a MusicBrainz artist URL ending in an MBID (UUID)",
    ),
    (
        "https://www.wikidata.org/wiki/",
        "wikidata.org",
        "a Wikidata entity URL ending in a Q-number",
    ),
    (
        "https://www.discogs.com/artist/",
        "discogs.com",
        "a Discogs artist URL shaped /artist/<id>-<Slug> (Discogs addresses "
        "artists by numeric id, never by a bare slug)",
    ),
)
_REGISTRY_PATTERNS = {
    "musicbrainz.org": _MUSICBRAINZ_ARTIST,
    "wikidata.org": _WIKIDATA_ENTITY,
    "discogs.com": _DISCOGS_ARTIST,
}


def citation_problem(kind: SourceKind, citation: str) -> Optional[str]:
    """Why ``citation`` cannot locate a record for ``kind``, or ``None`` if it can.

    Deliberately *not* enforced in ``IdentityEvidence.__post_init__``: evidence is
    a plain value object built throughout the suite (including property tests that
    generate arbitrary citations), and a constructor invariant would impose a
    contract the rest of the codebase was not written against. It is a gate the
    shipped fixture is held to instead (``tests/test_demo_citations.py``), which
    is where the failure actually was.

    Two layers, answering different questions:

    * **By kind** — a ``MUSICBRAINZ_GENDER`` or ``WIKIDATA_P21`` claim has to cite
      the registry it names. This is the original check.
    * **By host** — any citation pointing into a known registry has to be an
      address that registry can resolve. This is what catches the shape the demo
      shipped for its two ``DISCOGS_LINEUP`` claims (``/artist/big-thief``):
      exempt from the by-kind layer, and pointing at a registry that addresses
      artists by numeric id.

    This function checks the **shape** of an identifier, never its **subject**.
    ``https://www.wikidata.org/wiki/Q16735549`` is a well-formed Wikidata locator
    that resolves to a Cypriot footballer; nothing here can tell you it was cited
    for Mitski. Subject is what ``tests/test_demo_citations.py``'s verified-subject
    ledger is for.
    """
    text = (citation or "").strip()
    if not text:
        return "is empty"
    if kind is SourceKind.MUSICBRAINZ_GENDER and not _MUSICBRAINZ_ARTIST.match(text):
        return "is not a MusicBrainz artist URL ending in an MBID (UUID)"
    if kind is SourceKind.WIKIDATA_P21 and not _WIKIDATA_ENTITY.match(text):
        return "is not a Wikidata entity URL ending in a Q-number"
    for prefix, host, expected in _REGISTRY_LOCATORS:
        if text.startswith(prefix) and not _REGISTRY_PATTERNS[host].match(text):
            return f"claims {host} but is not {expected}"
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


def normalise_asserted_value(kind: SourceKind, value: str) -> Optional[Gender]:
    """Public name for :func:`_map_value` — what gender a raw asserted value means.

    Callers outside the resolver need this to compare two *asserted* values
    without re-implementing the vocabulary: ``"female"`` and ``"woman"`` are the
    same claim, and ``"Q6581072"`` is that claim again from Wikidata. The
    corrections ledger uses it to decide whether an observed upstream value is
    the one a person proposed (:mod:`pipeline.corrections`). Returns ``None``
    for anything the controlled vocabulary does not cover — never a guess.
    """
    return _map_value(kind, value)


def resolve_identity(evidence: Sequence[IdentityEvidence]) -> IdentityLabel:
    """Resolve an individual's identity from permitted evidence only.

    Returns :data:`~pipeline.models.UNKNOWN_IDENTITY` when no permitted evidence
    yields a mappable gender. Never raises on *unknown* input — unknown is a
    normal, first-class answer. It does raise
    :class:`~pipeline.models.InferenceForbiddenError` on *non-permitted* input:
    :func:`assert_permitted_only` runs first, so a source kind that is not in
    the permitted sets fails loudly here rather than being silently skipped by
    the filter below.
    """
    assert_permitted_only(evidence)
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


def _map_orientation(kind: SourceKind, value: str) -> Optional[Orientation]:
    """Normalise one sourced orientation claim, or ``None``. Never a guess."""
    raw = value.strip()
    if kind is SourceKind.WIKIDATA_P91:
        return _ORIENTATION_QID_VOCAB.get(raw)
    return _ORIENTATION_FREEFORM_VOCAB.get(raw.lower())


def resolve_queer_identity(evidence: Sequence[IdentityEvidence]) -> QueerIdentity:
    """Resolve the second axis (ADR 0011) from permitted evidence only.

    Takes the *same* evidence list as :func:`resolve_identity` and reads a
    different question out of it, which is why nothing new has to be fetched:

    * **Orientation** comes from :data:`~pipeline.models.ORIENTATION_SOURCES`
      evidence — a P91 claim or the artist's own cited words. A statement the
      vocabulary does not cover contributes nothing.
    * **Trans self-identification** comes from the raw value a *gender* source
      asserted, which this module has always stored on
      :attr:`~pipeline.models.Source.detail` for provenance. A P21 claim of
      ``Q1052281`` says "trans woman"; :func:`resolve_identity` maps that to
      ``WOMAN`` and deliberately forgets the rest, and this reads it.

    Returns the first-class unknown on both halves when nothing maps — the
    normal answer for almost every artist, and never a negative claim about
    them. An artist with no sourced orientation is not thereby heterosexual, and
    one with no sourced trans self-identification is not thereby cis.
    """
    assert_permitted_only(evidence)
    mapped: list[tuple[IdentityEvidence, Orientation]] = []
    for ev in evidence:
        if ev.kind not in ORIENTATION_SOURCES:
            continue
        orientation = _map_orientation(ev.kind, ev.value)
        if orientation is not None:
            mapped.append((ev, orientation))

    orientation = Orientation.UNKNOWN
    orientation_sources: tuple[Source, ...] = ()
    if mapped:
        # Same priority rule as gender: the artist's own words outrank a
        # third-party registry entry, deterministically.
        mapped.sort(
            key=lambda pair: (
                -_SOURCE_PRIORITY.get(pair[0].kind, 0),
                pair[0].kind.value,
                pair[0].citation,
            )
        )
        orientation = mapped[0][1]
        orientation_sources = tuple(ev.as_source() for ev, _ in mapped)

    trans_sources = tuple(
        ev.as_source()
        for ev in evidence
        if ev.kind in INDIVIDUAL_IDENTITY_SOURCES and _asserts_trans_identity(ev.value)
    )
    return QueerIdentity(
        orientation=orientation,
        orientation_sources=orientation_sources,
        trans_self_identified=True if trans_sources else None,
        trans_sources=trans_sources,
    )


def _asserts_trans_identity(value: str) -> bool:
    """Whether a raw asserted value is itself a trans self-identification."""
    raw = value.strip()
    return raw in _TRANS_ASSERTED_VALUES or raw.lower() in _TRANS_ASSERTED_VALUES


def resolve_composition(
    fronts: Sequence[FrontPerson], evidence: Sequence[IdentityEvidence]
) -> Optional[BandComposition]:
    """Build sourced band composition from lineup/role evidence.

    ``fronts`` are the sourced front-people (each with their own resolved,
    possibly-unknown identity). ``evidence`` must be band-composition sources.
    Returns ``None`` when there is no sourced lineup — composition, like
    identity, defaults to unknown rather than to a guess. Raises
    :class:`~pipeline.models.InferenceForbiddenError` on non-permitted evidence,
    for the same reason :func:`resolve_identity` does.
    """
    assert_permitted_only(evidence)
    comp_sources = tuple(ev.as_source() for ev in evidence if ev.kind in BAND_COMPOSITION_SOURCES)
    if not comp_sources or not fronts:
        return None
    return BandComposition(members_fronting=tuple(fronts), sources=comp_sources)


def assert_permitted_only(evidence: Sequence[IdentityEvidence]) -> None:
    """Raise if any evidence carries a non-permitted kind. **On the running path.**

    Called at the top of :func:`resolve_identity` and :func:`resolve_composition`
    — the two entry points where untrusted evidence becomes a label — so this is
    a guard that executes, not one that documents an intention. Until #72 it had
    no caller outside its own test, which meant its test proved the ``if``
    worked rather than that anything was being guarded.

    Evidence is *normally* rejected earlier, at :class:`Source` construction, so
    with today's closed :class:`~pipeline.models.SourceKind` enum this cannot
    fire. That is exactly what it is for: if a future ``SourceKind`` member is
    added without being added to a permitted set, the resolver's filter would
    silently skip it and return ``UNKNOWN``. This turns that silence into a
    raise.
    """
    for ev in evidence:
        if ev.kind not in (
            INDIVIDUAL_IDENTITY_SOURCES | ORIENTATION_SOURCES | BAND_COMPOSITION_SOURCES
        ):
            raise InferenceForbiddenError(f"{ev.kind} is not a permitted source")
