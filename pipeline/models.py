"""Core domain models — where the project's hard guardrails live as invariants.

The README guardrails are enforced *here*, in the type system, not merely in tests:

1. Identity is **sourced, never inferred.** A non-``unknown`` gender cannot be
   constructed without at least one citation (:class:`Source`). There is no code
   path that derives gender from a name, voice, image, or genre — and there is no
   :class:`SourceKind` member that represents such a thing. ``woman`` includes
   trans women, explicitly: sourced self-identification is the only test, and the
   vocabulary deliberately contains no cis/trans distinction to draw.
2. ``unknown`` is **first-class.** It is a real :class:`Gender` member and the
   default for every artist. Downstream code must never penalise it; the re-rank
   layer is boost-only (see :mod:`recommender.rerank`).
3. **"Female-fronted" is band-composition metadata**, kept distinct from any
   individual's gender. It is a *tri-state, sourced* property on
   :class:`BandComposition`, never an inference and never a claim about a person.
   It is also **narrow**: it says only that a front-person's own sourced gender
   is ``WOMAN``. A band fronted by a sourced nonbinary artist is *not*
   "female-fronted" — nonbinary is never a subtype of woman here. The general,
   category-preserving fact is :attr:`BandComposition.sourced_front_genders`.
4. Every recommendation carries an :class:`Explanation` with non-empty signals,
   an identity basis, and the sources behind that basis.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Optional


class IdentityError(Exception):
    """Base class for identity-guardrail violations."""


class UnsourcedIdentityError(IdentityError):
    """Raised when a non-unknown identity is constructed without a citation."""


class InferenceForbiddenError(IdentityError):
    """Raised if a forbidden (inferred) basis is ever used for an identity."""


class Gender(enum.Enum):
    """Controlled self-identification vocabulary. ``UNKNOWN`` is first-class.

    These map to *sourced self-identification only*. They are never assigned by
    guessing. ``OTHER`` exists so that a sourced self-identification outside the
    common terms is representable rather than being flattened to ``UNKNOWN``.
    """

    WOMAN = "woman"
    MAN = "man"
    NONBINARY = "nonbinary"
    OTHER = "other"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


#: Genders the values-lens is configured to surface (sourced only). This is a
#: **lens policy** set, never a fact about the world: nothing that *asserts*
#: something (``BandComposition.female_fronted``, ``sourced_front_genders``,
#: the rendered identity phrase) may read it, or a future edit to the lens
#: would silently change what the data model claims. Only the lens and the
#: re-rank consult it. This is the
#: canonical aligned set, consumed by :class:`recommender.lens.LensSpec`
#: (:data:`recommender.lens.VALUES_LENS`) — it lives here, not in
#: ``recommender``, to avoid a circular import (``recommender`` already depends
#: on ``pipeline``). Note this is a *re-rank* concern, not an identity concern:
#: ``UNKNOWN`` is deliberately absent here yet is never penalised — see
#: :mod:`recommender.rerank`. ``Gender.OTHER``'s exclusion is likewise
#: deliberate, not an oversight: it is a heterogeneous sourced bucket (intersex,
#: third-gender, terms outside the common vocabulary) that does not map cleanly
#: to this lens's stated purpose of surfacing women and nonbinary artists; the
#: full rationale — and the fact this is revisable per an identity-data-ethics
#: review — is documented on :data:`recommender.lens.VALUES_LENS` and in
#: ``docs/audits/identity-data-ethics.md``.
VALUES_ALIGNED_GENDERS: frozenset[Gender] = frozenset({Gender.WOMAN, Gender.NONBINARY})


class IdentityBasis(enum.Enum):
    """*How* an identity label was established — never *guessed*."""

    SELF_IDENTIFIED = "self-identified"
    BAND_COMPOSITION = "band-composition"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return self.value


class SourceKind(enum.Enum):
    """The **only** permitted provenance kinds.

    Crucially, there is no member here for a name, a voice, an image, a genre,
    or any other heuristic. The no-inference guardrail test asserts that this
    enum contains none of those and that the resolver accepts nothing else.
    """

    # --- About an individual's self-identified gender -------------------------
    WIKIDATA_P21 = "wikidata-p21"  # "sex or gender" claim
    MUSICBRAINZ_GENDER = "musicbrainz-gender"  # editorial / self-reported field
    ARTIST_STATEMENT = "artist-statement"  # a cited public self-identification
    # --- About band lineup / role (composition only, NOT individual gender) ---
    DISCOGS_LINEUP = "discogs-lineup"
    MUSICBRAINZ_RELATIONSHIP = "musicbrainz-relationship"

    def __str__(self) -> str:
        return self.value


#: Sources that may establish an *individual's* gender.
INDIVIDUAL_IDENTITY_SOURCES: frozenset[SourceKind] = frozenset(
    {SourceKind.WIKIDATA_P21, SourceKind.MUSICBRAINZ_GENDER, SourceKind.ARTIST_STATEMENT}
)
#: Sources that may establish *band composition / lineup*.
BAND_COMPOSITION_SOURCES: frozenset[SourceKind] = frozenset(
    {SourceKind.DISCOGS_LINEUP, SourceKind.MUSICBRAINZ_RELATIONSHIP, SourceKind.ARTIST_STATEMENT}
)
#: Every permitted source kind. Equal to the enum's members, by construction.
PERMITTED_SOURCES: frozenset[SourceKind] = INDIVIDUAL_IDENTITY_SOURCES | BAND_COMPOSITION_SOURCES


@dataclass(frozen=True)
class Source:
    """A single citation for an identity claim, with a retrieval timestamp.

    The ``retrieved_at`` field gives every label data lineage (Quality §9).
    """

    kind: SourceKind
    citation: str  # stable reference: URL, Wikidata QID, MBID, etc.
    retrieved_at: str  # ISO-8601 date the claim was fetched
    detail: str = ""  # the raw value the source asserted (e.g. "female")
    #: True when this Source is a locally-entered correction (FIX-10) rather
    #: than an upstream-fetched claim. Still an ``ARTIST_STATEMENT`` — the
    #: "no citation, no override" invariant applies identically — but callers
    #: (why-cards, renderers) can label it distinctly for transparency.
    is_local_correction: bool = False

    def __post_init__(self) -> None:
        if not self.citation.strip():
            raise UnsourcedIdentityError("a Source must carry a non-empty citation")
        if self.kind not in PERMITTED_SOURCES:  # pragma: no cover - enum-exhaustive
            raise InferenceForbiddenError(f"{self.kind!r} is not a permitted source")
        if self.is_local_correction and self.kind is not SourceKind.ARTIST_STATEMENT:
            raise InferenceForbiddenError(
                "a local correction must be recorded as an ARTIST_STATEMENT source"
            )


def _validate_individual_sources(sources: tuple[Source, ...]) -> None:
    for source in sources:
        if source.kind not in INDIVIDUAL_IDENTITY_SOURCES:
            raise InferenceForbiddenError(
                f"{source.kind} cannot establish an individual's gender; "
                "it is a band-composition source"
            )


def _validate_conflict(conflict: bool, claims: tuple[Source, ...]) -> None:
    if not conflict:
        if claims:
            raise IdentityError("conflicting_claims requires conflict=True")
        return
    if len(claims) < 2:
        raise IdentityError("a conflict must carry at least two conflicting claims")
    _validate_individual_sources(claims)
    if len({source.detail for source in claims}) < 2:
        raise IdentityError(
            "conflict=True requires >=2 distinct asserted genders among conflicting_claims"
        )


@dataclass(frozen=True)
class IdentityLabel:
    """An artist's identity as *sourced*. Defaults to first-class ``UNKNOWN``.

    Invariants (checked at construction):

    * A non-``UNKNOWN`` gender requires at least one :class:`Source`.
    * That source must be an *individual-identity* source — a band-composition
      source can never establish a person's gender.
    * A non-``UNKNOWN`` gender's basis must be ``SELF_IDENTIFIED``.
    * ``conflict=True`` requires at least two distinct asserted genders among
      ``conflicting_claims`` (FIX-10) — surfacing disagreement is itself a
      sourced claim, never a bare assertion.
    """

    gender: Gender = Gender.UNKNOWN
    basis: IdentityBasis = IdentityBasis.UNKNOWN
    sources: tuple[Source, ...] = ()
    confidence: Optional[float] = None
    #: True when permitted sources disagreed on this individual's gender. The
    #: resolver still reports its highest-priority ``gender`` above, but a
    #: conflict is never silently hidden — the disagreeing sources are kept
    #: in ``conflicting_claims`` so it can be shown, not buried.
    conflict: bool = False
    #: The disagreeing sources behind a conflict (empty when ``conflict`` is
    #: False). A superset of, or equal to, ``sources`` in practice — kept as
    #: its own field so "what everyone asserted" survives independently of
    #: "which source we chose to report".
    conflicting_claims: tuple[Source, ...] = ()

    def __post_init__(self) -> None:
        if self.gender is Gender.UNKNOWN:
            # Unknown is first-class: no source required, basis must be UNKNOWN.
            if self.basis is not IdentityBasis.UNKNOWN:
                raise IdentityError("unknown gender must carry UNKNOWN basis")
            if self.conflict:
                raise IdentityError("an unknown gender cannot carry a conflict")
            return
        if not self.sources:
            raise UnsourcedIdentityError(
                f"gender {self.gender} has no source — identity is never inferred"
            )
        if self.basis is not IdentityBasis.SELF_IDENTIFIED:
            raise InferenceForbiddenError("an individual gender must have a SELF_IDENTIFIED basis")
        _validate_individual_sources(self.sources)
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise IdentityError("confidence must be in [0, 1]")
        _validate_conflict(self.conflict, self.conflicting_claims)

    @property
    def is_known(self) -> bool:
        return self.gender is not Gender.UNKNOWN


#: The singleton "we don't know, and that's fine" label.
UNKNOWN_IDENTITY = IdentityLabel()


@dataclass(frozen=True)
class FrontPerson:
    """A sourced member of a band's fronting lineup.

    Their ``identity`` is itself an :class:`IdentityLabel` — sourced or unknown.
    We never collapse a band-level property into a personal gender claim.
    """

    name: str
    role: str  # as stated by the source, e.g. "lead vocals"
    identity: IdentityLabel = field(default_factory=IdentityLabel)


@dataclass(frozen=True)
class BandComposition:
    """Sourced lineup/role info, kept strictly separate from any member's gender.

    :attr:`sourced_front_genders` is the general fact this class asserts: *which
    genders the sourced front-people are sourced as*. It preserves every
    category exactly as the source stated it — nonbinary stays nonbinary, and
    nothing is widened, collapsed, or defaulted on the way out.

    :attr:`female_fronted` is the narrow, historically-named special case of
    that fact, and is tri-state:

    * ``True``  — a sourced front-person's own sourced gender is ``WOMAN``;
    * ``None``  — unknown (no sources, no front-person, or no front-person whose
      sourced gender is ``WOMAN``).

    It is **never** ``False`` by inference: the absence of a sourced woman front
    is "unknown", not "male-fronted". It is also **never** ``True`` for a band
    whose only sourced front-person is nonbinary — that band is fronted by a
    nonbinary artist, and saying "female-fronted" would misgender them on the
    strength of the very value the source supplied. Callers that want "does this
    band's sourced lineup match this set of genders" must ask
    :meth:`has_sourced_front_person_in`, never re-purpose ``female_fronted``.
    """

    members_fronting: tuple[FrontPerson, ...] = ()
    sources: tuple[Source, ...] = ()

    def __post_init__(self) -> None:
        for src in self.sources:
            if src.kind not in BAND_COMPOSITION_SOURCES:
                raise InferenceForbiddenError(
                    f"{src.kind} is not a permitted band-composition source"
                )

    @property
    def sourced_front_genders(self) -> frozenset[Gender]:
        """The *sourced* genders of this band's front-people. A fact, not a policy.

        Empty when the lineup itself is unsourced or there is no front-person.
        ``UNKNOWN`` is excluded because it asserts nothing: "no sourced gender
        for this front-person" is not a gender, and including it would let an
        absence read as a claim.
        """
        if not self.sources:
            return frozenset()
        return frozenset(
            person.identity.gender
            for person in self.members_fronting
            if person.identity.gender is not Gender.UNKNOWN
        )

    def has_sourced_front_person_in(self, genders: Iterable[Gender]) -> bool:
        """True iff some front-person's *own sourced* gender is in ``genders``.

        The seam a lens asks its own question through, so a lens-policy change
        can never alter what :attr:`female_fronted` asserts about the world.
        """
        return bool(self.sourced_front_genders & frozenset(genders))

    @property
    def female_fronted(self) -> Optional[bool]:
        """Tri-state: ``True`` iff a front-person's *own sourced* gender is ``WOMAN``."""
        return True if Gender.WOMAN in self.sourced_front_genders else None


@dataclass(frozen=True)
class Artist:
    """An artist/band as known to the system. ``artist_id`` is a stable key."""

    artist_id: str
    name: str
    tags: tuple[str, ...] = ()
    identity: IdentityLabel = field(default_factory=IdentityLabel)
    composition: Optional[BandComposition] = None
    listeners: int = 0  # popularity proxy, for the baseline + debias check
    playcount: int = 0

    @property
    def sourced_front_genders(self) -> frozenset[Gender]:
        """The sourced genders of this act's front-people (empty for a solo act)."""
        return self.composition.sourced_front_genders if self.composition else frozenset()

    @property
    def female_fronted(self) -> Optional[bool]:
        """Tri-state, and narrow: ``True`` only for a sourced *woman* front-person."""
        return self.composition.female_fronted if self.composition else None

    @property
    def values_aligned(self) -> bool:
        """True iff *sourced* identity OR *sourced* composition aligns with the lens.

        Delegates to the default lens's semantics — equivalent to
        ``recommender.lens.VALUES_LENS.aligned(self)`` — kept here as a
        convenience property so callers that only care about the default lens
        don't need to import :mod:`recommender.lens`. :class:`recommender.lens.LensSpec`
        is the declared, inspectable manifest (name, aligned predicate, boost
        bound, rationale) that this property mirrors.

        Unknown returns ``False`` here — but "not aligned" must never translate
        into a penalty; it only means "received no boost". See the re-rank layer.

        The composition leg asks :meth:`BandComposition.has_sourced_front_person_in`
        rather than reading ``female_fronted``, so a band fronted only by a
        sourced nonbinary artist is still surfaced by this lens *without* the
        data model ever having to call that band "female-fronted".
        """
        if self.identity.gender in VALUES_ALIGNED_GENDERS:
            return True
        return bool(self.sourced_front_genders & VALUES_ALIGNED_GENDERS)


@dataclass(frozen=True)
class Scrobble:
    """A single play event from listening history."""

    artist_id: str
    artist_name: str
    track: str
    ts: int  # unix seconds


@dataclass(frozen=True)
class ListeningProfile:
    """A user's listening history, reduced to per-artist play weights + tags.

    ``play_counts`` holds *weights*, not necessarily integer counts: a plain
    build is exact play counts (float-valued for type uniformity), but a
    recency-decayed or era-windowed profile (:func:`pipeline.ingest.build_profile`)
    accumulates fractional weights into the same field.
    """

    username: str
    play_counts: dict[str, float]  # artist_id -> total play weight
    artist_names: dict[str, str]  # artist_id -> display name
    tags: dict[str, tuple[str, ...]]  # artist_id -> tags

    @property
    def known_artist_ids(self) -> frozenset[str]:
        return frozenset(self.play_counts)

    def top_artists(self, n: int) -> list[str]:
        """Artist ids by play count, descending, with id as a stable tie-break."""
        return [
            aid for aid, _ in sorted(self.play_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
        ]


@dataclass(frozen=True)
class Signal:
    """One reason a recommendation surfaced (the "why")."""

    kind: str  # "collaborative" | "content" | "rerank" | "popularity"
    detail: str
    weight: float


@dataclass(frozen=True)
class Explanation:
    """The full, human-readable justification attached to every recommendation."""

    signals: tuple[Signal, ...]
    identity_basis: IdentityBasis
    identity_sources: tuple[Source, ...]
    summary: str

    def __post_init__(self) -> None:
        if not self.signals:
            raise ValueError("every recommendation must carry at least one signal")
        if not self.summary.strip():
            raise ValueError("every recommendation must carry a non-empty summary")


@dataclass(frozen=True)
class Recommendation:
    """A scored, explained recommendation. Immutable; re-ranking returns copies."""

    artist: Artist
    base_score: float  # hybrid score before the values lens
    rerank_delta: float  # boost applied by the lens (>= 0, never negative)
    explanation: Explanation
    rank: int = 0
    base_rank: int = 0  # counterfactual rank at lens_strength=0 (pure taste)

    @property
    def score(self) -> float:
        return self.base_score + self.rerank_delta

    def with_rank(self, rank: int) -> Recommendation:
        return replace(self, rank=rank)

    def with_base_rank(self, base_rank: int) -> Recommendation:
        return replace(self, base_rank=base_rank)
