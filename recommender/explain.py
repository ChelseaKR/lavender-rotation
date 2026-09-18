"""Build the human-readable explanation attached to every recommendation.

Every recommendation must show *why* (the signals), the *identity basis*, and the
*source* of that basis (README + Transparency audit §D). The summary is honest
about unknown: an unknown artist is described as "surfaced on musical similarity
alone", never apologized for and never hidden.
"""

from __future__ import annotations

from pipeline.models import (
    Artist,
    Explanation,
    Gender,
    IdentityBasis,
    Signal,
    Source,
)

from recommender.collaborative import CollabResult
from recommender.content import ContentResult
from recommender.why import artist_identity_phrase


def build_explanation(
    artist: Artist,
    collab: CollabResult,
    content: ContentResult,
    rerank_delta: float,
    lens_strength: float,
) -> Explanation:
    """Assemble signals + identity basis + sources into an :class:`Explanation`."""
    signals: list[Signal] = []

    for c in collab.top_contributors(3):
        signals.append(
            Signal(
                kind="collaborative",
                detail=f"similar to {c.seed_name} ({c.similarity:.0%} match)",
                weight=round(c.similarity * c.seed_weight, 4),
            )
        )
    if content.overlap_tags:
        shown = ", ".join(content.overlap_tags[:4])
        signals.append(
            Signal(
                kind="content",
                detail=f"shared tags: {shown}",
                weight=round(content.score, 4),
            )
        )
    if rerank_delta > 0.0:
        signals.append(
            Signal(
                kind="rerank",
                detail=f"values lens boost (strength {lens_strength:.0%})",
                weight=round(rerank_delta, 4),
            )
        )

    # Guarantee a non-empty "why" even for a thin candidate.
    if not signals:
        signals.append(
            Signal(kind="content", detail="appears in your discovery catalog", weight=0.0)
        )

    # Identity basis + the *actual* citations behind it.
    if artist.identity.gender is not Gender.UNKNOWN:
        basis = IdentityBasis.SELF_IDENTIFIED
        sources: tuple[Source, ...] = artist.identity.sources
    elif artist.composition is not None and artist.sourced_front_genders:
        # Any sourced front-person gender — not just a woman's — is a real
        # band-composition basis. Gating this on `female_fronted` would have
        # made the basis (and therefore the citations shown) depend on *which*
        # gender was sourced, which is exactly the flattening this branch has
        # to avoid.
        basis = IdentityBasis.BAND_COMPOSITION
        sources = artist.composition.sources
    else:
        basis = IdentityBasis.UNKNOWN
        sources = ()

    summary = f"Recommended because {signals[0].detail}; {artist_identity_phrase(artist)}."
    return Explanation(
        signals=tuple(signals),
        identity_basis=basis,
        identity_sources=sources,
        # ADR 0011's second axis, carried separately (#92). It was computed by
        # `resolve_queer_identity` at ingest and then read by nothing: the lens
        # boosted on it while every surface showed only the *gender* citation,
        # so the one claim that put an artist in the queer lens was the one
        # claim a reader could not see. ADR 0011 §4 says the why-card shows the
        # raw asserted value and whether the artist stated it or a registry
        # recorded it; this is the field that makes that possible.
        #
        # Not gated on which lens is running. A citation only visible when a
        # particular flag is passed is not auditable, and CONTRIBUTING's
        # fix-at-source flow needs the artist (or the operator) to be able to
        # see and dispute the claim that is being held about them at all times.
        queer_sources=artist.queer.sources,
        summary=summary,
    )
