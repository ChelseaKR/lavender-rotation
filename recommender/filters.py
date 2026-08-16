"""Output filters: what a listener asks *not* to see. Not the lens.

Kept in its own module because it is a different mechanism from
:mod:`recommender.lens`, and conflating the two would quietly break the lens's
central promise. The lens is **boost-only**: it re-orders by adding a bounded,
non-negative amount, and every artist keeps their score. A filter *removes*. One
is a re-ranking policy, the other is a subtraction from the result set, and only
the second can make someone disappear.

They also answer to different owners. The lens is the product's stated value
judgement, argued for in ``LensSpec.rationale`` and audited in
``docs/audits/fairness-identity.md``. A filter is the listener's own preference
about their own discovery queue, off by default, chosen per run.

**Why "sourced man", and not "not values-aligned".** The obvious
implementation — keep only what the lens boosts — is wrong here, and wrong in
the way this project exists to prevent. ``values_aligned`` is ``False`` for an
artist whose gender is simply *unknown*, so filtering on it would delete every
unknown artist from the results: on the listening history this was written
against, that was 4 of 10 picks and 57 of 88 catalogued artists. Those are not
men. They are artists nobody has sourced — disproportionately the less-documented
ones, which on a gender-imbalanced upstream skews against exactly the artists
the lens is for. "Unknown is first-class and is never dropped" is a hard
guardrail (README), and a filter is precisely where it would be easiest to lose.

So this filter only ever removes a *positive* sourced claim: an artist sourced
as a man, or an act whose sourced fronting lineup is entirely sourced men. It
never removes an absence of a claim, and it never removes ``Gender.OTHER``,
which is a sourced identity outside the lens's scope rather than a man's.
"""

from __future__ import annotations

from pipeline.models import Artist, Gender


def is_sourced_man_only(artist: Artist) -> bool:
    """True iff every *sourced* gender claim about this act is a man's.

    Composition wins when there is one: for a band, the sourced fact is who
    fronts it, and a lineup with a sourced woman or nonbinary front-person is
    not "a sourced man" whatever else it contains — which is why an act fronted
    by both is kept. Falls back to the individual's own sourced gender for a
    solo act, which has no lineup.

    Returns ``False`` for an unknown artist, always: no claim is not a claim.
    """
    fronts = artist.sourced_front_genders
    if fronts:
        return fronts == frozenset({Gender.MAN})
    return artist.identity.gender is Gender.MAN
