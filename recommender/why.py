"""'Why this artist' — one honest, render-agnostic explanation per recommendation.

Every recommendation already carries an :class:`~pipeline.models.Explanation`
(signals + identity basis + sources). This module turns that into a single,
structured, *presentation-ready* object the dashboard, the static HTML renderer,
the CLI, and the playlist export can all share — so the identity wording and its
provenance are written **once**, not re-derived (and subtly diverged) in three UIs.

Two guarantees are made explicit in the output itself, not just in a comment:

* **Sourced, never inferred.** :class:`WhyThisArtist.inferred` is hard-coded
  ``False`` and every shown identity claim carries its citation, the *raw value
  the source asserted*, and the date it was retrieved.
* **Unknown is first-class.** An artist with no sourced identity is described as
  "surfaced on musical similarity alone" — a normal answer, never an apology and
  never a guess.

A third guarantee, added for FIX-10: **disagreement is surfaced, not hidden.**
When permitted sources disagree about an individual's gender, the resolver
still reports its highest-priority pick, but ``WhyThisArtist.conflict_note``
names every disagreeing source and what it asserted — worded neutrally, never
as an apology or a guess at who's "right".
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.models import (
    Artist,
    Gender,
    IdentityBasis,
    IdentityLabel,
    Recommendation,
    Source,
    SourceKind,
)

#: The one place ADR 0011's second axis is named in a rendered surface. Written
#: once, like every other identity phrase in this module, so the wording cannot
#: drift between the CLI, the static render, the dashboard and the report. It
#: says "orientation / trans" rather than "queer" because those are the two
#: questions the citations answer; "queer" is the *lens's* word for a policy set
#: (:data:`recommender.lens.QUEER_ORIENTATIONS`), not a claim any source made.
QUEER_SOURCES_HEADING = "Orientation / trans sources (sourced, never inferred)"


@dataclass(frozen=True)
class ProvenanceItem:
    """One citation behind an identity claim, made fully transparent.

    Mirrors a :class:`~pipeline.models.Source` but flattened for display: the
    ``asserted_value`` is the *raw* thing the source said (e.g. ``"female"``,
    ``"Q6581072"``) so a reader can audit the claim, not just trust the label.
    """

    source_kind: str
    asserted_value: str
    citation: str
    retrieved_at: str
    #: True when this citation is a locally-entered correction (FIX-10) rather
    #: than an upstream-fetched claim — surfaced distinctly so a reader can
    #: tell "we were told this by the artist/operator, with a citation" apart
    #: from "an external database asserted this".
    is_local_correction: bool = False

    @classmethod
    def from_source(cls, source: Source) -> ProvenanceItem:
        return cls(
            source_kind=str(source.kind),
            asserted_value=source.detail,
            citation=source.citation,
            retrieved_at=source.retrieved_at,
            is_local_correction=source.is_local_correction,
        )


@dataclass(frozen=True)
class WhyThisArtist:
    """The complete, honest justification for one recommendation.

    * ``headline`` — the single strongest reason, for a glanceable summary.
    * ``reasons`` — every "why recommended" signal, as readable lines.
    * ``identity_statement`` — the sourced identity (or the first-class unknown).
    * ``identity_basis`` — *how* the identity was established (never "inferred").
    * ``provenance`` — the citations behind the identity claim (empty if unknown).
    * ``inferred`` — always ``False``; identity in this system is never guessed.
    * ``conflict_note`` — non-empty, neutral wording of a source disagreement
      (FIX-10); empty string when sources agree (or identity is unknown).
    * ``queer_provenance`` — the citations behind ADR 0011's second axis
      (orientation, trans self-identification), kept in their own tuple rather
      than merged into ``provenance``. They are evidence about a different
      question from a different source with different failure modes, and a
      reader must be able to tell which claim rests on which citation. Empty for
      almost every artist, which is the normal, first-class answer and never
      means "not queer".
    """

    artist_name: str
    headline: str
    reasons: tuple[str, ...]
    identity_statement: str
    identity_basis: IdentityBasis
    provenance: tuple[ProvenanceItem, ...]
    inferred: bool = False
    conflict_note: str = ""
    rank_shift: str = "the values lens did not change this pick's position"
    queer_provenance: tuple[ProvenanceItem, ...] = ()

    @property
    def identity_is_known(self) -> bool:
        return self.identity_basis is not IdentityBasis.UNKNOWN

    def to_text(self) -> str:
        """A plain-text block suitable for a CLI or an export comment."""
        lines = [
            f"Why {self.artist_name}: {self.headline}",
            f"  Identity: {self.identity_statement}",
            f"  Rank shift: {self.rank_shift}",
        ]
        if self.conflict_note:
            lines.append(f"  {self.conflict_note}")
        if self.reasons:
            lines.append("  Why recommended:")
            lines.extend(f"    - {reason}" for reason in self.reasons)
        if self.provenance:
            lines.append("  Sources (sourced, never inferred):")
            lines.extend(
                f"    - {p.source_kind} asserted {p.asserted_value!r} "
                f"({p.citation}, retrieved {p.retrieved_at})"
                for p in self.provenance
            )
        else:
            lines.append("  Sources: none — identity unknown, surfaced on merit.")
        if self.queer_provenance:
            lines.append(f"  {QUEER_SOURCES_HEADING}:")
            lines.extend(
                f"    - {p.source_kind} asserted {p.asserted_value!r} "
                f"({p.citation}, retrieved {p.retrieved_at})"
                for p in self.queer_provenance
            )
        return "\n".join(lines)

    def to_markdown(self) -> str:
        """A Markdown block (used by the CLI and any Markdown-aware surface)."""
        parts = [
            f"**Why {self.artist_name}** — {self.headline}",
            "",
            f"_Identity:_ {self.identity_statement}",
            f"_Rank shift:_ {self.rank_shift}",
        ]
        if self.conflict_note:
            parts.append("")
            parts.append(f"_{self.conflict_note}_")
        if self.reasons:
            parts.append("")
            parts.append("**Why recommended**")
            parts.extend(f"- {reason}" for reason in self.reasons)
        if self.provenance:
            parts.append("")
            parts.append("**Sources** (sourced, never inferred)")
            parts.extend(
                f"- {p.source_kind} asserted `{p.asserted_value}` — "
                f"[{p.citation}]({p.citation}) (retrieved {p.retrieved_at})"
                for p in self.provenance
            )
        else:
            parts.append("")
            parts.append("_Sources: none — identity unknown, surfaced on merit._")
        if self.queer_provenance:
            parts.append("")
            parts.append(f"**{QUEER_SOURCES_HEADING}**")
            parts.extend(
                f"- {p.source_kind} asserted `{p.asserted_value}` — "
                f"[{p.citation}]({p.citation}) (retrieved {p.retrieved_at})"
                for p in self.queer_provenance
            )
        return "\n".join(parts)


def _confidence_tier(label: IdentityLabel) -> str:
    """Describe the strongest cited source without interpreting its score."""
    source_kinds = {source.kind for source in label.sources}
    if SourceKind.ARTIST_STATEMENT in source_kinds:
        return "directly stated by the artist"
    if SourceKind.WIKIDATA_P21 in source_kinds:
        return "recorded in Wikidata"
    if SourceKind.MUSICBRAINZ_GENDER in source_kinds:
        return "editorial database entry"
    return "cited source"


#: Display order for a band's sourced front-person genders, so the rendered
#: phrase is deterministic regardless of lineup order.
_FRONT_GENDER_ORDER: tuple[Gender, ...] = (
    Gender.WOMAN,
    Gender.NONBINARY,
    Gender.MAN,
    Gender.OTHER,
)

#: How each *sourced* front-person gender is named, as ``(singular, plural)``.
#: There is deliberately no ``UNKNOWN`` entry — a front-person with no sourced
#: gender contributes nothing to say — and deliberately no entry that widens one
#: gender into another. ``OTHER`` describes what is sourced rather than naming a
#: category the source did not name, because the honest label for that bucket is
#: not knowable from here.
_FRONT_GENDER_NOUN: dict[Gender, tuple[str, str]] = {
    Gender.WOMAN: ("a sourced woman", "sourced women"),
    Gender.NONBINARY: ("a sourced nonbinary artist", "sourced nonbinary artists"),
    Gender.MAN: ("a sourced man", "sourced men"),
    Gender.OTHER: (
        "a front-person whose sourced self-identification is outside this vocabulary",
        "front-people whose sourced self-identifications are outside this vocabulary",
    ),
}


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _sourced_front_gender_counts(artist: Artist) -> dict[Gender, int]:
    """How many front-people carry each *sourced* gender (unknown ones excluded)."""
    if artist.composition is None or not artist.sourced_front_genders:
        return {}
    counts: dict[Gender, int] = {}
    for person in artist.composition.members_fronting:
        gender = person.identity.gender
        if gender is not Gender.UNKNOWN:
            counts[gender] = counts.get(gender, 0) + 1
    return counts


def band_front_phrase(artist: Artist) -> str:
    """Name a band's *sourced* front-person genders, or ``""`` when none are sourced.

    This is the one place a band-composition label is written, and it names the
    genders the lineup source actually asserted. It never widens a category to
    reach a more familiar word: a band whose only sourced front-person is
    nonbinary reads as "fronted by a sourced nonbinary artist", never
    "female-fronted band". The trailing clause is deliberately *not* "distinct
    from any member's gender" — the phrase is derived from named members' own
    sourced self-identifications, so claiming otherwise would be false; what it
    is silent about is everyone else in the band.
    """
    counts = _sourced_front_gender_counts(artist)
    if not counts:
        return ""
    fronts = _join(
        [
            _FRONT_GENDER_NOUN[gender][0 if counts[gender] == 1 else 1]
            for gender in _FRONT_GENDER_ORDER
            if gender in counts
        ]
    )
    return (
        f"band fronted by {fronts} (sourced lineup; each gender here is that "
        "front-person's own sourced self-identification, and no gender is "
        "claimed for any other member)"
    )


def artist_identity_phrase(artist: Artist) -> str:
    """The single sourced-or-unknown identity sentence, written in one place.

    Re-used by the explanation summary, the dashboard, the HTML renderer, and the
    export so the phrasing never drifts. Honest about unknown; never inferred;
    never a category the sources did not assert.
    """
    label = artist.identity
    if label.gender is not Gender.UNKNOWN:
        tier = _confidence_tier(label)
        suffix = f" ({tier})" if tier else ""
        return f"{label.gender}, self-identified{suffix}"
    front = band_front_phrase(artist)
    if front:
        return front
    return "unknown — surfaced on musical similarity alone"


def identity_statement(rec: Recommendation) -> str:
    """Identity sentence for a whole recommendation (delegates to the artist phrase)."""
    return artist_identity_phrase(rec.artist)


def conflict_note(artist: Artist) -> str:
    """A respectful, neutral note when permitted sources disagreed (FIX-10).

    Empty string when there is no conflict. Phrasing states what each source
    asserted plus its retrieval date — never an apology, never a guess at
    which source is "right".
    """
    label = artist.identity
    if not label.conflict:
        return ""
    claims = "; ".join(
        f"{src.kind} asserted {src.detail!r} (retrieved {src.retrieved_at})"
        for src in label.conflicting_claims
    )
    return f"Sources disagree: {claims}"


def _reason_line(kind: str, detail: str) -> str:
    return f"{kind}: {detail}"


def rank_shift_statement(lens_rank: int, base_rank: int) -> str:
    """Explain **lens** movement against the pure-taste counterfactual.

    Both arguments are positions in the same, unfiltered ordering: *base_rank*
    before the lens, *lens_rank* immediately after it. The difference is
    therefore the lens and nothing else.

    It used to take the *displayed* rank, which is assigned after the
    identity-blind serendipity pass and after the listener's
    ``hide_sourced_men`` subtraction. ``rank - base_rank`` absorbed all three
    causes while this sentence named only the first, so raising the Serendipity
    slider produced cards telling a listener that the identity lens had promoted
    a sourced man and demoted a nonbinary artist — at ``--lens 0``, where every
    ``rerank_delta`` in the run is ``0.0`` (#113).

    A zero *base_rank* or *lens_rank* means the rank was never recorded, which
    is not the same as "the lens did not move it" but renders the same way:
    claiming a movement from an unrecorded position would be the same
    manufactured attribution in another direction.
    """
    if base_rank == 0 or lens_rank == 0 or lens_rank == base_rank:
        return "the values lens did not change this pick's position"
    return f"the values lens moved this pick from #{base_rank} to #{lens_rank}"


def why_this_artist(rec: Recommendation) -> WhyThisArtist:
    """Build the shared, transparent 'why this artist' view for a recommendation."""
    expl = rec.explanation
    reasons = (
        *(_reason_line(s.kind, s.detail) for s in expl.signals),
        "ranking: popularity is not an input; this pick is taste, optional "
        "per-artist feedback, and the bounded sourced-identity lens",
    )
    headline = expl.signals[0].detail if expl.signals else "in your discovery catalog"
    provenance = tuple(ProvenanceItem.from_source(s) for s in expl.identity_sources)
    # Kept out of `provenance` on purpose: an orientation citation is not a
    # gender citation, and merging them would let a P91 claim be read as the
    # basis for a gender label — the exact leak ADR 0011 keeps the two axes
    # apart to prevent.
    #
    # Not deduplicated against the gender list either. One document can answer
    # both questions: a Wikidata P21 claim of `Q1052281` is the citation for
    # `Gender.WOMAN` *and* the citation for a trans self-identification, and it
    # is listed under both headings because it is evidence for both claims. The
    # heading is what tells a reader which question a citation was read for;
    # showing the document once, under the gender heading only, would hide the
    # second reading — which is the whole defect this closes.
    queer_provenance = tuple(ProvenanceItem.from_source(s) for s in expl.queer_sources)
    return WhyThisArtist(
        artist_name=rec.artist.name,
        headline=headline,
        reasons=reasons,
        identity_statement=identity_statement(rec),
        identity_basis=expl.identity_basis,
        provenance=provenance,
        inferred=False,
        conflict_note=conflict_note(rec.artist),
        # `lens_rank` for the lens sentence, never `rank` (#113).
        # `recommend()` stamps it on everything it emits; a hand-built
        # `Recommendation` that carries only `rank` has had no serendipity pass
        # and no output filter run over it, so there `rank` *is* the lens-only
        # position and there is nothing to misattribute.
        rank_shift=rank_shift_statement(rec.lens_rank or rec.rank, rec.base_rank),
        queer_provenance=queer_provenance,
    )
