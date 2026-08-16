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

**This is a re-rank concern, never a penalty.** Exactly like ``UNKNOWN``,
``Gender.OTHER`` is simply not in the aligned set: it receives zero boost and
keeps its exact base score. It is never down-ranked, never excluded from
results, and never treated differently from ``UNKNOWN`` by the scoring math —
see :mod:`recommender.rerank` for the boost-only invariant that makes this
mechanically true.
"""

from __future__ import annotations

from dataclasses import dataclass

from pipeline.models import VALUES_ALIGNED_GENDERS, Artist, Gender


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
        return bool(artist.sourced_front_genders & self.aligned_genders)

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
        "Re-rank concern, never a penalty: like UNKNOWN, an artist sourced as "
        "Gender.OTHER (or MAN, or anyone unaligned) receives zero boost and keeps "
        "their exact base score — never down-ranked, never dropped, never treated "
        "worse than an unknown-identity artist. The boost-only invariant "
        "(recommender/rerank.py) makes this mechanically true, not just a promise."
    ),
)
