"""Computed exposure & rank-fairness metrics for the values lens (FIX-05).

Turns the fairness *narrative* (``docs/audits/fairness-identity.md``) into
*generated numbers* committed to ``docs/audits/eval-report.json``. Nothing here
infers gender: every segment is read from an artist's **sourced** identity (or
sourced band composition). The residual ``unknown`` segment is first-class and is
the subject of the merge-blocking retention guarantee below.

Metric choices (short justification, per FIX-05's requirement):

* **Exposure@k** — the *count-based* share of the top-``k`` recommendation slots a
  segment occupies. It is the simplest, most legible allocation measure; we report
  it per lens strength so the lens's re-allocation is visible. (Attention-weighted
  exposure — discounting lower ranks — is a defensible alternative; we prefer the
  unweighted share because the list is short and every surfaced slot is seen.)
* **Unknown-retention@k** — the fraction of pure-taste unknown artists in the
  evaluated top-``k`` whose score, top-``k`` presence, and rank are preserved as
  the lens strengthens. It is *verified on emitted output*, not inferred from a
  boost-only score formula — see :func:`assert_unknown_retained`.
* **Rank-shift** — the mean change in list position per segment relative to pure
  taste (lens 0). Aligned artists can re-order sourced non-unknown slots, while
  unknown slots stay pinned to their pure-taste positions.
* **Popularity-tier x identity** — cross-tabs the candidate pool by listener count
  (:attr:`~pipeline.models.Artist.listeners`), surfacing the "lens over-favours
  already-popular women" allocational risk named in ``fairness-identity.md`` §3.
"""

from __future__ import annotations

from pipeline.models import Artist, Gender, Recommendation

#: Identity segments (sourced-only; ``unknown`` is first-class and never inferred).
WOMAN = "woman"
NONBINARY = "nonbinary"
#: A band whose sourced lineup is fronted by someone whose *own* sourced gender
#: is ``WOMAN``. Strictly that, so a nonbinary front-person is never counted
#: here — they get :data:`NONBINARY_FRONTED`.
FEMALE_FRONTED = "female-fronted"
#: A band whose sourced lineup is fronted by someone whose own sourced gender is
#: ``NONBINARY`` (and by no sourced woman).
NONBINARY_FRONTED = "nonbinary-fronted"
MAN = "man"
OTHER = "other"
UNKNOWN = "unknown"

#: Emitted in a fixed order for a stable, diffable report.
SEGMENTS: tuple[str, ...] = (
    WOMAN,
    NONBINARY,
    FEMALE_FRONTED,
    NONBINARY_FRONTED,
    MAN,
    OTHER,
    UNKNOWN,
)

#: Popularity tiers by listener count (the allocational-risk cross-tab axis).
TIERS: tuple[str, ...] = ("niche", "mid", "popular")
_NICHE_CEILING = 100_000
_MID_CEILING = 1_000_000


class FairnessAssertionError(AssertionError):
    """Raised when the emitted output violates the unknown-retention guarantee."""


def identity_segment(artist: Artist) -> str:
    """Segment an artist by *sourced* identity, then sourced lineup, else unknown.

    Individual sourced gender wins. Failing that, the band's *sourced* lineup is
    read at the granularity the source actually asserted: a band fronted by a
    sourced woman is ``female-fronted``, and a band fronted only by a sourced
    nonbinary artist is ``nonbinary-fronted`` — never folded into
    ``female-fronted``, which would report a person's sourced gender as a
    different one in a document that is committed and published.

    A band whose only sourced front-people are men, or are sourced outside the
    vocabulary, stays in first-class ``unknown``: the act's own gender is
    unsourced, it receives no boost, and it is pinned to its pure-taste slot
    exactly like any other unknown — which is what this report measures. That
    grouping is unchanged from before and is asserted to agree with
    :func:`recommender.rerank.is_unknown_artist`. No inference is ever performed
    here.
    """
    gender = artist.identity.gender
    if gender is Gender.WOMAN:
        return WOMAN
    if gender is Gender.NONBINARY:
        return NONBINARY
    if gender is Gender.MAN:
        return MAN
    if gender is Gender.OTHER:
        return OTHER
    # Sourced band composition — a fact about the lineup, not a personal claim
    # about the act, and reported as the gender the source actually asserted.
    fronts = artist.sourced_front_genders
    if Gender.WOMAN in fronts:
        return FEMALE_FRONTED
    if Gender.NONBINARY in fronts:
        return NONBINARY_FRONTED
    return UNKNOWN


def popularity_tier(listeners: int) -> str:
    """Bucket a listener count into a coarse popularity tier."""
    if listeners < _NICHE_CEILING:
        return "niche"
    if listeners < _MID_CEILING:
        return "mid"
    return "popular"


def _lens_key(lens_strength: float) -> str:
    """Stable string key for a lens strength (JSON object keys must be strings)."""
    return f"{lens_strength:.2f}"


def exposure_at_k(recs: list[Recommendation], k: int) -> dict[str, float]:
    """Share of the top-``k`` recommendation slots held by each identity segment."""
    if k < 1:
        raise ValueError("k must be at least 1")
    top = recs[:k]
    counts = dict.fromkeys(SEGMENTS, 0)
    for rec in top:
        counts[identity_segment(rec.artist)] += 1
    n = len(top)
    return {seg: round(counts[seg] / n, 4) if n else 0.0 for seg in SEGMENTS}


def _unknown_state(recs: list[Recommendation], k: int) -> dict[str, tuple[float, int]]:
    """artist_id -> (score, one-based rank) for unknown artists in the top-k."""
    if k < 1:
        raise ValueError("k must be at least 1")
    return {
        rec.artist.artist_id: (rec.score, rank)
        for rank, rec in enumerate(recs[:k], start=1)
        if identity_segment(rec.artist) == UNKNOWN
    }


def unknown_retention(
    recs_by_lens: dict[float, list[Recommendation]], *, k: int, base_lens: float = 0.0
) -> dict[str, float]:
    """Per-lens fraction of pure-taste top-k unknowns without score/rank loss.

    An artist is retained only if it remains in the emitted top-k, its score is not
    lower, and its one-based rank is no worse than under pure taste.
    """
    base = _unknown_state(recs_by_lens[base_lens], k)
    out: dict[str, float] = {}
    for lens in sorted(recs_by_lens):
        present = _unknown_state(recs_by_lens[lens], k)
        if not base:
            out[_lens_key(lens)] = 1.0
            continue
        retained = sum(
            1
            for aid, (base_score, base_rank) in base.items()
            if aid in present and present[aid][0] >= base_score and present[aid][1] <= base_rank
        )
        out[_lens_key(lens)] = round(retained / len(base), 4)
    return out


def _unknown_downranked_count(
    recs_by_lens: dict[float, list[Recommendation]], *, k: int, base_lens: float = 0.0
) -> int:
    """Count unknown/lens pairs dropped from top-k or losing score/rank."""
    base = _unknown_state(recs_by_lens[base_lens], k)
    count = 0
    for lens, recs in recs_by_lens.items():
        if lens == base_lens:
            continue
        present = _unknown_state(recs, k)
        for aid, (base_score, base_rank) in base.items():
            if aid not in present or present[aid][0] < base_score or present[aid][1] > base_rank:
                count += 1
    return count


def assert_unknown_retained(
    recs_by_lens: dict[float, list[Recommendation]], *, k: int, base_lens: float = 0.0
) -> None:
    """Merge-blocking guarantee, checked on emitted output: unknown is never penalised.

    Raises :class:`FairnessAssertionError` if, at any lens strength, an
    ``unknown``-identity artist surfaced in pure taste's top-k is dropped from the
    top-k, has its score lowered, or moves to a worse rank.
    """
    base = _unknown_state(recs_by_lens[base_lens], k)
    for lens in sorted(recs_by_lens):
        present = _unknown_state(recs_by_lens[lens], k)
        for aid, (base_score, base_rank) in base.items():
            if aid not in present:
                raise FairnessAssertionError(
                    f"unknown artist {aid!r} dropped from top-{k} at lens {lens}"
                )
            score, rank = present[aid]
            if score < base_score:
                raise FairnessAssertionError(
                    f"unknown artist {aid!r} lost score at lens {lens}: {base_score} -> {score}"
                )
            if rank > base_rank:
                raise FairnessAssertionError(
                    f"unknown artist {aid!r} lost rank at lens {lens}: {base_rank} -> {rank}"
                )


def rank_shift_by_segment(
    base_recs: list[Recommendation], lens_recs: list[Recommendation]
) -> dict[str, float]:
    """Mean change in list position per segment, ``lens`` vs pure taste (negative = up)."""
    base_rank = {r.artist.artist_id: i for i, r in enumerate(base_recs, start=1)}
    shifts: dict[str, list[int]] = {seg: [] for seg in SEGMENTS}
    for i, rec in enumerate(lens_recs, start=1):
        aid = rec.artist.artist_id
        if aid in base_rank:
            shifts[identity_segment(rec.artist)].append(i - base_rank[aid])
    return {seg: round(sum(v) / len(v), 4) if v else 0.0 for seg, v in shifts.items()}


def popularity_identity_crosstab(recs: list[Recommendation]) -> dict[str, dict[str, int]]:
    """Cross-tab the candidate pool: popularity tier x identity segment (counts)."""
    table: dict[str, dict[str, int]] = {tier: dict.fromkeys(SEGMENTS, 0) for tier in TIERS}
    for rec in recs:
        table[popularity_tier(rec.artist.listeners)][identity_segment(rec.artist)] += 1
    return table


def exposure_report(
    recs_by_lens: dict[float, list[Recommendation]], *, k: int, base_lens: float = 0.0
) -> dict[str, object]:
    """Assemble the JSON-able fairness block emitted into ``eval-report.json``.

    ``recs_by_lens`` maps a lens strength to the *full* ranked output at that lens.
    The ``guarantees`` sub-block carries the merge-blocking signal the CLI checks.
    """
    lenses = sorted(recs_by_lens)
    retention = unknown_retention(recs_by_lens, k=k, base_lens=base_lens)
    downranked = _unknown_downranked_count(recs_by_lens, k=k, base_lens=base_lens)
    min_retention = min(retention.values()) if retention else 1.0
    return {
        "k": k,
        "lens_strengths": lenses,
        "segments": list(SEGMENTS),
        "exposure_at_k": {_lens_key(s): exposure_at_k(recs_by_lens[s], k) for s in lenses},
        "unknown_retention": retention,
        "mean_rank_shift": {
            _lens_key(s): rank_shift_by_segment(recs_by_lens[base_lens], recs_by_lens[s])
            for s in lenses
            if s != base_lens
        },
        "popularity_identity_crosstab": popularity_identity_crosstab(recs_by_lens[base_lens]),
        "guarantees": {
            "unknown_retention_all_lenses": min_retention >= 1.0 and downranked == 0,
            "min_unknown_retention": min_retention,
            "unknown_downranked_count": downranked,
        },
    }


def observability_panel(
    recs_by_lens: dict[float, list[Recommendation]],
    current_lens: float,
    *,
    k: int,
    base_lens: float = 0.0,
) -> dict[str, object]:
    """Reshape the generated fairness metrics into table-ready UI rows."""
    if base_lens not in recs_by_lens:
        raise ValueError(f"base_lens {base_lens!r} not present in recs_by_lens")
    if current_lens not in recs_by_lens:
        raise ValueError(f"current_lens {current_lens!r} not present in recs_by_lens")
    base_exposure = exposure_at_k(recs_by_lens[base_lens], k)
    current_exposure = exposure_at_k(recs_by_lens[current_lens], k)
    retention = unknown_retention(recs_by_lens, k=k, base_lens=base_lens)
    return {
        "k": k,
        "base_lens": base_lens,
        "current_lens": current_lens,
        "segments": list(SEGMENTS),
        "exposure_rows": [
            {
                "segment": segment,
                "base_share": base_exposure[segment],
                "current_share": current_exposure[segment],
            }
            for segment in SEGMENTS
        ],
        "retention_row": {"segment": UNKNOWN, "by_lens": retention},
        "rank_shift_row": rank_shift_by_segment(
            recs_by_lens[base_lens], recs_by_lens[current_lens]
        ),
    }
