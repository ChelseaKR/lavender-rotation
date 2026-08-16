"""``LensSpec`` — the values lens as a declared, inspectable object.

Before this module, "what does the values lens boost, and why" was answered by
reading constants scattered across ``recommender/rerank.py`` and
``pipeline/models.py``. :class:`LensSpec` collects that into one manifest — an
aligned predicate over *sourced* fields only, a boost bound, and human-readable
rationale + harms text — so the dashboard, tests, and future lenses can all
introspect the same object instead of re-deriving its meaning.

**The ``Gender.OTHER`` question, decided explicitly.** ``Gender.OTHER`` is a
*sourced* self-identification outside the common vocabulary (e.g. intersex,
third-gender terms) — see :class:`pipeline.models.Gender`. This lens's aligned
set (:data:`VALUES_LENS.aligned_genders`) does **not** include it. That is a
deliberate choice, not an oversight, for one reason: ``OTHER`` is a
heterogeneous bucket covering disparate identities that were never unified by
the act of sourcing them, and folding it into "aligned with a women-and-
nonbinary lens" would make an unstated value claim on those artists' behalf
about which lens they belong to. Excluding it keeps the lens's stated purpose —
surfacing women and nonbinary artists — from silently expanding to cover
identities it was never scoped to represent. This is a revisable decision: a
dedicated lens for artists sourced as ``OTHER`` (or a broader "sourced marginalized
gender" lens that explicitly opts them in) is a legitimate future LensSpec, but
that is a new manifest with its own rationale and harms note, gated on an
identity-data-ethics review — not a silent addition to this one. See
``docs/audits/identity-data-ethics.md`` for the recorded decision.

**Not boosted, and not displaced either.** Not being in the aligned set means
``Gender.OTHER`` receives zero boost and keeps its exact base score. It also
keeps its exact pure-taste *position*: sourced ``OTHER`` is rank-protected
alongside ``UNKNOWN`` (:data:`recommender.rerank.RANK_PROTECTED_GENDERS`), and
:func:`recommender.exposure.assert_other_retained` checks that on emitted output
at every merge. That protection was added by #68, which found this module
promising it in text the dashboard renders while the re-rank pinned only unknown
slots — a sourced ``OTHER`` artist could be pushed below a *lower-scoring*
unknown one. Declining to speak for a heterogeneous sourced bucket is a reason
not to boost it; it was never a reason to move it down.

**What this lens does re-allocate, stated once.** A boosted artist that rises
has to pass someone. Every group except sourced men is now held, so the lens's
re-allocation lands on sourced men: their score is never reduced, their position
can be. Pinning them too would leave aligned artists able to permute only among
their own base slots, i.e. a lens that cannot change exposure at any strength.
``harms_note`` says exactly this, in place of the "never down-ranked, never
treated worse than an unknown-identity artist" wording it carried before, which
was measurably untrue.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.models import QUEER_ORIENTATIONS, VALUES_ALIGNED_GENDERS, Artist, Gender, Orientation


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class LensSpec:
    """A declared, inspectable values lens: who it boosts, how much, and why.

    * ``aligned_genders`` — the *sourced* genders this lens treats as aligned.
    * ``max_boost`` — the largest non-negative boost the lens can add, at full
      strength, as a fraction of the (roughly ``[0, 1]``-normalised) score scale.
    * ``rationale`` — manifest text answering "what does this lens boost, and
      why" for humans (surfaced in the UI; see :mod:`app.dashboard`).
    * ``harms_note`` — the lens's own honest account of the value judgement it
      makes and the harm it could cause if misapplied.
    """

    name: str
    aligned_genders: frozenset[Gender]
    max_boost: float
    rationale: str
    harms_note: str

    #: Optional second-axis policy (ADR 0011). When set, an artist also aligns
    #: if their *sourced* orientation is in this set, or — when
    #: ``include_trans_self_identified`` — a permitted source asserted a trans
    #: self-identification. Both legs are gated by ``queer_gate_genders`` so a
    #: lens scoped to "queer women and nonbinary people" does not silently widen
    #: to everyone queer.
    aligned_orientations: frozenset[Orientation] = frozenset()
    include_trans_self_identified: bool = False
    queer_gate_genders: frozenset[Gender] = frozenset()

    def aligned(self, artist: Artist) -> bool:
        """True iff *sourced* identity or *sourced* composition aligns with this lens.

        Reads **sourced fields only** — ``artist.identity.gender`` (never
        inferred; see :class:`pipeline.models.IdentityLabel`) and
        ``artist.sourced_front_genders`` (never inferred; see
        :class:`pipeline.models.BandComposition`). Never raises: an ``UNKNOWN``
        or unaligned gender, or a band with no sourced front-person, simply
        evaluates to ``False`` — never a penalty, just "no boost". See the
        module docstring for why ``Gender.OTHER`` is excluded from
        ``aligned_genders`` by default.

        The composition leg intersects this lens's *own* aligned set with the
        genders the lineup source actually asserted, rather than reading
        ``female_fronted``. That keeps the question the lens asks ("is a front
        person aligned with me?") separate from the claim the data model makes
        ("a front person is a sourced woman"), so widening or narrowing this
        lens can never change what the model asserts — and a band fronted only
        by a sourced nonbinary artist is aligned here without ever being
        described as "female-fronted".
        """
        if artist.identity.gender in self.aligned_genders:
            return True
        if self._queer_aligned(artist):
            return True
        return bool(artist.sourced_front_genders & self.aligned_genders)

    def _queer_aligned(self, artist: Artist) -> bool:
        """The second axis (ADR 0011), gated on gender so the scope stays stated.

        ``queer_gate_genders`` is what keeps "queer *women* and nonbinary
        people" from quietly becoming "everyone queer": a sourced queer claim
        only aligns an artist whose sourced gender is in the gate. An artist of
        unknown gender is not gated *into* the lens — we cannot establish they
        are a woman — and, as everywhere else, is never penalised for it: they
        keep their pure-taste score and their rank-protected position.
        """
        if not self.queer_gate_genders:
            return False
        if artist.identity.gender not in self.queer_gate_genders:
            return False
        if artist.queer.orientation in self.aligned_orientations:
            return True
        return bool(self.include_trans_self_identified and artist.queer.trans_self_identified)

    def boost(self, artist: Artist, strength: float) -> float:
        """The non-negative boost for ``artist`` at lens ``strength`` ∈ [0, 1].

        Zero unless :meth:`aligned` is true. Never exceeds ``max_boost`` and is
        never negative — the boost-only invariant lives here as well as in
        :mod:`recommender.rerank`, which delegates to this method.
        """
        if strength <= 0.0 or not self.aligned(artist):
            return 0.0
        return self.max_boost * _clamp01(strength)


#: The default, shipped values lens: sourced women & nonbinary artists.
VALUES_LENS = LensSpec(
    name="Sourced women & nonbinary artists",
    aligned_genders=VALUES_ALIGNED_GENDERS,
    max_boost=0.5,
    rationale=(
        "Boosts artists whose gender is *sourced* (never inferred) as a woman or "
        "nonbinary person, and bands whose sourced lineup is fronted by someone "
        "whose own sourced gender is one of those. A band fronted only by a "
        "sourced nonbinary artist is boosted as such; it is never relabelled "
        "'female-fronted' to get there. "
        "Purpose: counteract the well-documented under-exposure of women and "
        "nonbinary musicians in popularity-driven recommendation, without ever "
        "penalising anyone — including artists whose identity is unknown or "
        "unsourced, who always keep their exact base score. Boost is bounded to "
        "0.5 (of a ~[0, 1] score scale) at full strength so taste signal always "
        "still matters; a lens strength slider in [0, 1] lets a listener dial the "
        "boost, including off. "
        "On Gender.OTHER: OTHER is deliberately EXCLUDED from this lens's aligned "
        "set. OTHER is a heterogeneous sourced bucket (e.g. intersex, third-gender, "
        "or other self-identifications outside the common vocabulary) that does "
        "not map cleanly onto this lens's stated purpose of surfacing women and "
        "nonbinary artists specifically; including it would make an unstated value "
        "claim that those disparate identities belong to this particular lens. "
        "This exclusion is revisable — a distinct, explicitly-scoped lens for "
        "OTHER-sourced artists is the right way to expand coverage, gated on an "
        "identity-data-ethics review (see docs/audits/identity-data-ethics.md), "
        "not a silent addition here."
    ),
    harms_note=(
        "What this lens does to artists it does not boost, stated exactly. "
        "SCORE, for everyone: no artist's score is ever reduced. An unaligned "
        "artist — sourced as Gender.OTHER or MAN, or of unknown identity — "
        "receives a boost of exactly zero and keeps their exact base score at "
        "every lens strength. Checked on emitted output at every merge by "
        "recommender/exposure.py::assert_no_score_reduced. "
        "POSITION, for two groups: artists of unknown identity, and artists "
        "sourced as Gender.OTHER, also keep their exact pure-taste position "
        "(assert_unknown_retained / assert_other_retained). "
        "POSITION, for sourced men: not held. A boosted artist that rises has "
        "to pass someone, and everyone else is held, so this lens's whole "
        "re-allocation is exposure moving from sourced men to sourced women and "
        "nonbinary artists. Their scores are untouched; their list positions "
        "can move down. That is the value judgement, and this note states it "
        "rather than denying it: until #68 this paragraph promised that nobody "
        "unaligned was ever down-ranked or treated worse than an "
        "unknown-identity artist, and the ranking did not do that. "
        "WHAT THIS LENS CANNOT DO, since a listener will ask: it cannot remove "
        "anyone. The boost is bounded, so a sourced man with a high enough "
        "taste score survives it at any strength — which is not a defect, it is "
        "what boost-only means. A listener who wants them gone entirely is "
        "asking for a filter, and that is a separate, opt-in mechanism they "
        "choose per run (recommender/filters.py), never something this lens "
        "does on their behalf. That separation is load-bearing: a filter can "
        "make an artist disappear, and the one built here removes only a "
        "positive sourced claim, never an unknown artist."
    ),
)


#: The queer lens (ADR 0011): sourced queer women, and sourced nonbinary artists.
#:
#: Two legs, deliberately asymmetric. **Nonbinary artists align on gender
#: alone** — they are the most sparsely documented group here, and demanding a
#: second, rarer disclosure of them would surface the fewest of exactly the
#: people the lens exists for. **Women align on a sourced queer claim**: an
#: orientation in :data:`~pipeline.models.QUEER_ORIENTATIONS`, or a sourced
#: trans self-identification.
#:
#: Sourced men are out of scope by design, not by oversight — a gay man is not
#: what "queer women and nonbinary people" names. Nothing about that is a
#: judgement on him, and like every other unaligned artist he keeps his exact
#: score.
QUEER_LENS = LensSpec(
    name="Sourced queer women & nonbinary artists",
    aligned_genders=frozenset({Gender.NONBINARY}),
    aligned_orientations=QUEER_ORIENTATIONS,
    include_trans_self_identified=True,
    queer_gate_genders=frozenset({Gender.WOMAN}),
    max_boost=0.5,
    rationale=(
        "Surfaces artists whose own sourced self-identification is nonbinary, or "
        "is a woman together with a sourced queer orientation or trans "
        "self-identification. Every claim is cited and shown with the raw value "
        "its source asserted, and the card says whether the artist stated it or a "
        "registry recorded it — Wikidata's P91 is admitted for coverage but is "
        "more often a biographer's characterisation than someone's own words. "
        "Asexuality and demisexuality are recorded and not boosted: whether the "
        "ace spectrum sits under a queer lens is contested among ace people, and "
        "answering that silently would speak for them (revisable, ADR 0011)."
    ),
    harms_note=(
        "This lens reads the most sensitive data this project holds, and the "
        "honest statement is that the protection is now procedural rather than "
        "structural. SCORE, for everyone: unchanged — no artist's score is ever "
        "reduced, and an unaligned or unknown artist keeps their exact base "
        "score at every strength. UNKNOWN: almost every artist is unknown on "
        "this axis and always will be, because most people have never made a "
        "public statement a registry recorded. Unknown here must never be read "
        "as 'not queer' — it means nobody sourced it, and unknown artists keep "
        "their rank-protected pure-taste position. OUTING: a cache that records "
        "who is queer or trans is dangerous for real people in much of the "
        "world; identity never leaves the machine (no export carries it, "
        "tests/test_export_schema.py), it is local-only, and every claim is "
        "correctable at its source. COVERAGE SKEW: sourced queerness skews "
        "toward the already-famous, Anglophone, living and out, which is the "
        "opposite of who a discovery tool should favour — so this lens boosts "
        "rather than filters, and a listener seeing few picks is looking at a "
        "gap in the world's records, not at the world."
    ),
)

#: Every shipped lens, by the name a caller selects it with.
LENSES: dict[str, LensSpec] = {"women-nonbinary": VALUES_LENS, "queer": QUEER_LENS}
