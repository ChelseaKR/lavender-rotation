"""The values-aware re-rank — **boost-only**, with an explicit protected set.

Two different guarantees live here, and this module keeps them apart because
conflating them is what made the lens's published harms note untrue (#68):

**Score.** The lens can only *add* a non-negative boost to artists whose
*sourced* identity or *sourced* composition aligns with it. It can never
subtract. Every artist — aligned or not — keeps at least its exact base score at
every lens strength. This one holds for everybody, without exception, and is
checked on emitted output by
:func:`recommender.exposure.assert_no_score_reduced`.

**Position.** Score does not determine position on its own, because some slots
are *pinned*. :data:`RANK_PROTECTED_GENDERS` names who keeps their exact
pure-taste position: artists of ``UNKNOWN`` identity, and artists sourced as
``Gender.OTHER``. Aligned artists re-order only the remaining slots, so a
protected artist can never fall below its pure-taste position or disappear at a
top-k boundary (:func:`recommender.exposure.assert_unknown_retained`,
:func:`~recommender.exposure.assert_other_retained`).

A boosted artist that rises has to pass *someone*, so position cannot be held
for everyone at once while the lens still does anything. The artists it is not
held for are **sourced men**: their score is untouched, their position can move
down. That is this lens's value judgement, and ``VALUES_LENS.harms_note`` states
it in those words rather than promising a protection that is arithmetically
unavailable.

``lens_strength`` ∈ [0, 1] is surfaced in the UI and explained. At 0 the ranking
is identical to the pure hybrid ranking. The maximum boost is bounded so the lens
re-orders without obliterating the underlying taste signal.

The lens itself is a declared, inspectable :class:`~recommender.lens.LensSpec`
(:data:`recommender.lens.VALUES_LENS`) — the aligned predicate, boost bound, and
rationale (including the explicit ``Gender.OTHER`` decision) live there, not as
loose constants here.
"""

from __future__ import annotations

from dataclasses import replace

from pipeline.models import Artist, Gender, Recommendation

from recommender.lens import VALUES_LENS, LensSpec

#: Backward-compatible alias for :data:`recommender.lens.VALUES_LENS`'s boost
#: bound. Prefer importing ``VALUES_LENS`` directly for new code — this stays
#: for existing imports (e.g. ``tests/test_rerank.py``).
MAX_BOOST = VALUES_LENS.max_boost


def values_boost_for_artist(
    artist: Artist, lens_strength: float, lens: LensSpec = VALUES_LENS
) -> float:
    """The non-negative boost for an artist. Zero unless *sourced*-aligned.

    Delegates to :meth:`recommender.lens.LensSpec.boost` on the default
    :data:`~recommender.lens.VALUES_LENS`.
    """
    return lens.boost(artist, lens_strength)


def values_boost(rec: Recommendation, lens_strength: float) -> float:
    """The non-negative boost for one recommendation. Zero unless sourced-aligned."""
    return values_boost_for_artist(rec.artist, lens_strength)


def sort_and_rank(recs: list[Recommendation]) -> list[Recommendation]:
    """Deterministic ordering: score desc, then artist_id asc; assign 1-based rank."""
    ordered = sorted(recs, key=lambda r: (-r.score, r.artist.artist_id))
    return [rec.with_rank(i + 1) for i, rec in enumerate(ordered)]


#: Genders whose **pure-taste position** the re-rank holds, not merely whose
#: score it leaves alone.
#:
#: * ``UNKNOWN`` — "unknown is first-class and never down-ranked" is a README
#:   guardrail with its own merge-blocking check.
#: * ``OTHER`` — the lens declined to *boost* this bucket on the recorded
#:   grounds that it could not responsibly speak for a heterogeneous set of
#:   sourced self-identifications (see :mod:`recommender.lens`). That is a
#:   reason not to boost them. It was never a reason to displace them, and
#:   before #68 they were displaced — an artist who told the project who they
#:   are was ranked below a lower-scoring artist who had not, purely because
#:   the latter was in the protected set. Holding their slot costs the lens
#:   nothing it is entitled to.
#:
#: ``MAN`` is deliberately absent, and this is the whole of the lens's
#: re-allocation: pinning every unaligned artist would leave aligned artists
#: able to permute only among their own base slots, which makes the lens a
#: no-op — exposure@k could not change at any strength. See ``harms_note``.
RANK_PROTECTED_GENDERS: frozenset[Gender] = frozenset({Gender.UNKNOWN, Gender.OTHER})


def is_unknown_artist(artist: Artist) -> bool:
    """Match the fairness report's ``unknown`` segmentation without a cycle.

    Reads ``values_aligned`` rather than ``female_fronted`` so that a band whose
    sourced lineup is fronted only by a nonbinary artist — which *does* receive
    a boost — is not counted as unknown. ``tests/test_exposure.py`` asserts this
    stays equivalent to ``identity_segment(artist) == UNKNOWN``.
    """
    return artist.identity.gender is Gender.UNKNOWN and not artist.values_aligned


def is_rank_protected(artist: Artist) -> bool:
    """True iff this artist keeps its exact pure-taste position under the lens.

    An artist the lens *boosts* is never protected: pinning a boosted artist to
    its pure-taste slot would silently discard the boost. So an ``OTHER``-sourced
    solo artist is protected, while an ``OTHER``-sourced artist fronting an
    aligned band is movable — it is being paid, not displaced.
    """
    return artist.identity.gender in RANK_PROTECTED_GENDERS and not artist.values_aligned


def rerank(recs: list[Recommendation], lens_strength: float) -> list[Recommendation]:
    """Apply the boost-only lens while holding every rank-protected artist's base slot.

    Raises ``ValueError`` for a lens strength outside [0, 1].
    """
    if not (0.0 <= lens_strength <= 1.0):
        raise ValueError("lens_strength must be in [0, 1]")

    base_order = sorted(recs, key=lambda r: (-r.base_score, r.artist.artist_id))
    boosted: list[Recommendation] = []
    for rec in base_order:
        delta = values_boost(rec, lens_strength)
        assert delta >= 0.0  # invariant: the lens never penalises
        boosted.append(replace(rec, rerank_delta=delta))

    movable = sorted(
        (rec for rec in boosted if not is_rank_protected(rec.artist)),
        key=lambda r: (-r.score, r.artist.artist_id),
    )
    movable_iter = iter(movable)
    ordered = [rec if is_rank_protected(rec.artist) else next(movable_iter) for rec in boosted]

    return [rec.with_rank(i + 1) for i, rec in enumerate(ordered)]
