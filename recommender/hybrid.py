"""Hybrid recommender: combine collaborative + content, then apply the values lens.

The base score is a convex blend ``alpha * collaborative + (1 - alpha) * content``,
each signal min-max normalised across candidates so neither dominates by scale.
Optional, artist-scoped thumbs feedback applies a bounded nudge to that base
score; it never reads or generalises across identity.
The values lens is then applied **boost-only** (see :mod:`recommender.rerank`),
followed by an optional identity-blind serendipity/diversification pass over the
movable candidates (see :mod:`recommender.diversify`). The full list is
reconstructed around the rank-protected slots — unknown and sourced-``OTHER``
artists, see :data:`recommender.rerank.RANK_PROTECTED_GENDERS` — before top-k is
selected, and every result is explained.

At ``lens_strength = 0`` and ``explore = 0`` (both defaults) the output is the
pure-taste hybrid ranking — which is what the offline eval compares against
the popularity baseline.

Every recommendation also carries a ``base_rank``: its counterfactual position
in that pure-taste ordering (``lens_strength = 0``), computed *before* the lens
is applied. This lets every why-card say, in plain language, how the lens moved
a pick — see :mod:`recommender.why`. Serendipity reordering happens after this
counterfactual is recorded, so it cannot be misattributed to the values lens.
"""

from __future__ import annotations

from pipeline.lastfm import ScrobbleSource
from pipeline.models import Artist, ListeningProfile, Recommendation

from recommender.collaborative import CollabResult, collaborative_scores
from recommender.content import ContentResult, content_scores
from recommender.diversify import diversify
from recommender.explain import build_explanation
from recommender.feedback import Feedback, feedback_adjustment
from recommender.filters import is_sourced_man_only
from recommender.lens import VALUES_LENS, LensSpec
from recommender.rerank import is_rank_protected, rerank, values_boost_for_artist


def _normalise(value: float, peak: float) -> float:
    return value / peak if peak > 0.0 else 0.0


def recommend(
    profile: ListeningProfile,
    catalog: dict[str, Artist],
    source: ScrobbleSource,
    *,
    k: int = 20,
    alpha: float = 0.5,
    lens_strength: float = 0.0,
    explore: float = 0.0,
    feedbacks: list[Feedback] | None = None,
    feedback_strength: float = 1.0,
    hide_sourced_men: bool = False,
    lens: LensSpec = VALUES_LENS,
) -> list[Recommendation]:
    """Produce the top-``k`` explained recommendations.

    ``alpha`` weights collaborative vs content (0 = content only, 1 = collab only).
    ``lens_strength`` ∈ [0, 1] controls the values lens; 0 = pure taste ranking.
    ``explore`` ∈ [0, 1] controls the serendipity/diversification pass (see
    :mod:`recommender.diversify`); 0 = pure relevance ranking (default,
    unchanged behaviour — this is what the offline eval compares against the
    popularity baseline), 1 = maximum tag-space diversity.
    ``feedbacks`` contains the listener's current per-artist votes. The bounded
    adjustment is part of the taste-side base score, before the values lens.
    ``lens`` selects which declared :class:`~recommender.lens.LensSpec` boosts —
    the shipped women/nonbinary one by default, or the queer lens (ADR 0011).
    ``hide_sourced_men`` is the listener's opt-in output filter — off by default,
    so the eval and every existing caller are unaffected. It is deliberately not
    the lens: see :mod:`recommender.filters` for why it removes only a positive
    sourced claim and never an unknown artist.
    """
    if not (0.0 <= alpha <= 1.0):
        raise ValueError("alpha must be in [0, 1]")

    collab = collaborative_scores(profile, source)
    known = profile.known_artist_ids
    known_names = profile.known_artist_names
    # Candidates must be enriched (present in catalog) and not already known —
    # by id *or* by name. The name check is not redundant: the same artist can
    # be keyed by MBID in one upstream payload and by name in another, and on a
    # real listening history that aliasing served heavily-played artists back as
    # discoveries. See `ListeningProfile.known_artist_names`.
    candidates = {
        aid
        for aid in (set(collab) | set(catalog))
        if aid in catalog
        and aid not in known
        and catalog[aid].name.strip().casefold() not in known_names
    }
    content = content_scores(profile, catalog, candidates)

    collab_peak = max((collab[a].score for a in candidates if a in collab), default=0.0)
    content_peak = max((content[a].score for a in candidates), default=0.0)

    recs: list[Recommendation] = []
    for aid in sorted(candidates):
        artist = catalog[aid]
        c_res = collab.get(aid, CollabResult())
        t_res = content.get(aid, ContentResult())
        base = alpha * _normalise(c_res.score, collab_peak) + (1 - alpha) * _normalise(
            t_res.score, content_peak
        )
        base += feedback_adjustment(
            artist, feedbacks or (), feedback_strength, username=profile.username
        )
        delta = values_boost_for_artist(artist, lens_strength, lens)
        explanation = build_explanation(artist, c_res, t_res, delta, lens_strength)
        recs.append(
            Recommendation(
                artist=artist,
                base_score=round(base, 6),
                rerank_delta=round(delta, 6),
                explanation=explanation,
            )
        )

    # Counterfactual pure-taste rank (lens_strength=0), keyed on base_score, so every
    # card can say how (or whether) the values lens moved it. At lens_strength=0 this is
    # identical to the lens-applied order by construction (score == base_score).
    base_ordered = sorted(recs, key=lambda r: (-r.base_score, r.artist.artist_id))
    base_rank_of = {r.artist.artist_id: i + 1 for i, r in enumerate(base_ordered)}
    recs = [rec.with_base_rank(base_rank_of[rec.artist.artist_id]) for rec in recs]

    ranked = rerank(recs, lens_strength, lens)

    # Serendipity remains identity-blind inside diversify(). At this orchestration
    # boundary, pass it only the movable candidates, then reconstruct the full list
    # around the slots already protected by rerank(). Otherwise an MMR pass
    # after reranking can silently undo the absolute top-k/rank guarantee — for
    # sourced-OTHER artists as well as unknown ones (#68).
    movable = [rec for rec in ranked if not is_rank_protected(rec.artist, lens)]
    diversified = iter(diversify(movable, explore))
    protected = [
        rec if is_rank_protected(rec.artist, lens) else next(diversified) for rec in ranked
    ]

    # The listener's own opt-in subtraction, applied last — after ranking, after
    # rank protection, and after `base_rank` was recorded. Doing it here rather
    # than by dropping candidates earlier keeps every remaining pick's "the lens
    # moved this from #19 to #7" true of the real pure-taste ordering, instead
    # of a counterfactual computed over a pre-filtered world. See
    # `recommender.filters` for why this removes only a positive sourced claim.
    if hide_sourced_men:
        protected = [rec for rec in protected if not is_sourced_man_only(rec.artist)]

    limit = max(0, min(k, len(protected)))
    return [rec.with_rank(i + 1) for i, rec in enumerate(protected[:limit])]
