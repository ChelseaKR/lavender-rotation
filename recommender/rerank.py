"""The values-aware re-rank — **boost-only**, so unknown is never penalised.

This is where the project's central fairness guarantee is implemented and made
mechanically true: the lens can only *add* a non-negative boost to artists whose
*sourced* identity or *sourced* composition aligns with the lens. It can never
subtract. Therefore:

* an artist with an UNKNOWN identity keeps its exact base score and base rank;
* a sourced woman/nonbinary/female-fronted artist may move *up*;
* aligned artists re-order only the non-unknown slots, so an unknown artist can
  never fall below the pure-taste position or disappear at a top-k boundary.

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

from recommender.lens import VALUES_LENS

#: Backward-compatible alias for :data:`recommender.lens.VALUES_LENS`'s boost
#: bound. Prefer importing ``VALUES_LENS`` directly for new code — this stays
#: for existing imports (e.g. ``tests/test_rerank.py``).
MAX_BOOST = VALUES_LENS.max_boost


def values_boost_for_artist(artist: Artist, lens_strength: float) -> float:
    """The non-negative boost for an artist. Zero unless *sourced*-aligned.

    Delegates to :meth:`recommender.lens.LensSpec.boost` on the default
    :data:`~recommender.lens.VALUES_LENS`.
    """
    return VALUES_LENS.boost(artist, lens_strength)


def values_boost(rec: Recommendation, lens_strength: float) -> float:
    """The non-negative boost for one recommendation. Zero unless sourced-aligned."""
    return values_boost_for_artist(rec.artist, lens_strength)


def sort_and_rank(recs: list[Recommendation]) -> list[Recommendation]:
    """Deterministic ordering: score desc, then artist_id asc; assign 1-based rank."""
    ordered = sorted(recs, key=lambda r: (-r.score, r.artist.artist_id))
    return [rec.with_rank(i + 1) for i, rec in enumerate(ordered)]


def is_unknown_artist(artist: Artist) -> bool:
    """Match the fairness report's sourced-identity segmentation without a cycle.

    Reads ``values_aligned`` rather than ``female_fronted`` so that a band whose
    sourced lineup is fronted only by a nonbinary artist — which *does* receive
    a boost — is movable rather than pinned. Pinning a boosted artist to its
    pure-taste slot would silently discard the boost.
    ``tests/test_exposure.py`` asserts this stays equivalent to
    ``identity_segment(artist) == UNKNOWN``.
    """
    return artist.identity.gender is Gender.UNKNOWN and not artist.values_aligned


def rerank(recs: list[Recommendation], lens_strength: float) -> list[Recommendation]:
    """Apply the boost-only lens while protecting every unknown artist's base slot.

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
        (rec for rec in boosted if not is_unknown_artist(rec.artist)),
        key=lambda r: (-r.score, r.artist.artist_id),
    )
    movable_iter = iter(movable)
    ordered = [rec if is_unknown_artist(rec.artist) else next(movable_iter) for rec in boosted]

    return [rec.with_rank(i + 1) for i, rec in enumerate(ordered)]
