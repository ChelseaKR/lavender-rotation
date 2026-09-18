"""Per-recommendation thumbs feedback — a bounded, artist-scoped ranking nudge.

M6 "Should": let a listener thumbs a specific recommendation up or down and have
that opinion tune *future* rankings, without reopening the identity-fairness
guarantee the values lens depends on (:mod:`recommender.rerank`).

The mechanism is deliberately narrow:

* **Artist-scoped, never identity-scoped.** A vote is keyed to one
  ``artist_id``. :func:`feedback_adjustment` only ever folds in votes whose
  ``artist_id`` matches the artist being scored — a thumbs-down on one artist
  can never lower any other artist, let alone a whole identity class. Identity
  never appears in this module at all.
* **Bounded, like the lens's boost.** Votes are summed and squashed through
  ``tanh`` so the signal saturates rather than growing without bound, then
  scaled by :data:`MAX_FEEDBACK` — the negative-capable counterpart of
  :data:`recommender.rerank.MAX_BOOST`. The result always lies in
  ``(-MAX_FEEDBACK, MAX_FEEDBACK)``.
* **Composed into the base (taste) score, not the lens delta.** The lens's
  ``rerank_delta`` stays boost-only and non-negative (its own invariant,
  tested in ``tests/test_rerank.py``); feedback instead nudges
  ``base_score`` in :func:`recommender.hybrid.recommend`, before the lens is
  applied and the list is re-sorted. That keeps "the lens never penalizes
  unknown identity" and "feedback can raise or lower one artist" as two
  independent, non-interfering guarantees.
* **Deterministic.** Pure function of ``(artist, feedbacks, strength)``; vote
  order and repetition don't matter beyond their sum.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from pipeline.models import Artist

#: The largest adjustment feedback can add *or* subtract, mirroring
#: ``rerank.MAX_BOOST`` but signed: thumbs-down is allowed to lower an artist
#: (unlike the values lens, which is boost-only). Bounded so accumulated votes
#: reorder without ever swamping the underlying taste signal.
MAX_FEEDBACK = 0.3


@dataclass(frozen=True)
class Feedback:
    """One thumbs vote on one artist, for one user, with a lineage timestamp."""

    username: str
    artist_id: str
    vote: int  # +1 (thumbs-up) or -1 (thumbs-down)
    ts: int  # unix seconds the vote was recorded

    def __post_init__(self) -> None:
        if self.vote not in (1, -1):
            raise ValueError("vote must be +1 or -1")
        if not self.username.strip() or not self.artist_id.strip():
            raise ValueError("username and artist_id must be non-empty")


def feedback_adjustment(
    artist: Artist,
    feedbacks: Iterable[Feedback],
    strength: float = 1.0,
    *,
    username: str | None = None,
) -> float:
    """A bounded, artist-scoped score delta from accumulated thumbs feedback.

    Only rows whose ``artist_id`` matches ``artist.artist_id`` count. Votes are
    summed and passed through ``tanh`` so the delta saturates rather than
    growing without bound, then scaled by :data:`MAX_FEEDBACK`. The result
    always lies strictly within ``[-MAX_FEEDBACK, MAX_FEEDBACK]``.

    ``strength`` behaves like ``rerank.lens_strength``: 0 disables feedback
    entirely, 1 is full strength; values outside ``[0, 1]`` are clamped.

    No matching feedback (or a net-zero vote count) returns exactly ``0.0`` —
    feedback is opt-in and never changes an unvoted artist's score.
    """
    net_votes = sum(
        item.vote
        for item in feedbacks
        if item.artist_id == artist.artist_id and (username is None or item.username == username)
    )
    if net_votes == 0:
        return 0.0
    clamped_strength = min(1.0, max(0.0, strength))
    return MAX_FEEDBACK * math.tanh(net_votes * clamped_strength)
