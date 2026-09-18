"""The serendipity control's guardrail test — mirrors ``test_no_inference.py``.

EXP-04's pitch is a diversification pass that is *provably* identity-blind: it
must move recommendations around using only tag-space similarity and the
already-computed relevance score, never touching ``identity``,
``composition``, ``values_aligned``, or any gender-shaped field. This test
proves that three ways, in the same spirit as the identity resolver's guard:

1. **Code** — an AST scan of ``recommender/diversify.py``'s own functions
   proves they never read a forbidden attribute/name.
2. **Behavioral** — ``explore=0`` is a no-op re-order (identity to the input
   order); higher ``explore`` measurably raises intra-list tag diversity.
3. **Invariant** — the output is a permutation of the input: same artist_ids,
   same multiset of scores. The diversifier only re-orders; it never
   penalizes or alters a score (the FIX-05 exposure contract survives it).

Plus explicit validation of the ``explore`` range.
"""

from __future__ import annotations

import ast
import itertools
from pathlib import Path

import pytest
from pipeline.models import Explanation, IdentityBasis, Recommendation, Signal
from recommender import diversify as diversify_module
from recommender.diversify import diversify

from .conftest import make_artist

FORBIDDEN_ATTRS = frozenset(
    {
        "identity",
        "composition",
        "values_aligned",
        "gender",
        "front_person",
        "basis",
        "sources",
    }
)


def _rec(artist_id: str, score: float, tags: tuple[str, ...]) -> Recommendation:
    artist = make_artist(artist_id, tags=tags)
    return Recommendation(
        artist=artist,
        base_score=score,
        rerank_delta=0.0,
        explanation=Explanation(
            signals=(Signal("content", "d", 1.0),),
            identity_basis=IdentityBasis.UNKNOWN,
            identity_sources=(),
            summary="why",
        ),
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _mean_pairwise_jaccard_distance(recs: list[Recommendation]) -> float:
    tag_sets = [frozenset(r.artist.tags) for r in recs]
    pairs = list(itertools.combinations(tag_sets, 2))
    if not pairs:
        return 0.0
    return sum(1.0 - _jaccard(a, b) for a, b in pairs) / len(pairs)


# --- Code guard: an AST scan of the diversifier's own functions ------------


def test_diversifier_code_never_reads_a_forbidden_attribute() -> None:
    """AST scan: diversify.py's functions never touch identity-shaped fields.

    Catches a future regression where someone wires ``artist.identity`` or
    ``artist.values_aligned`` into the diversification decision — the whole
    point of a *identity-blind* serendipity control.
    """
    source = Path(diversify_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Attribute):
                    referenced.add(sub.attr.lower())
                elif isinstance(sub, ast.Name):
                    referenced.add(sub.id.lower())

    leaked = referenced & FORBIDDEN_ATTRS
    assert not leaked, f"diversify.py references forbidden attributes: {leaked}"


# --- Behavioral -------------------------------------------------------------


def test_explore_zero_is_identity_to_input_order() -> None:
    """``explore=0`` returns the input order unchanged; ranks are reassigned."""
    recs = [
        _rec("a", 0.9, ("pop", "dance")),
        _rec("b", 0.7, ("jazz", "piano")),
        _rec("c", 0.5, ("folk", "acoustic")),
    ]
    out = diversify(recs, 0.0)
    assert [r.artist.artist_id for r in out] == ["a", "b", "c"]
    assert [r.rank for r in out] == [1, 2, 3]
    # Ranks are reassigned, not merely copied, but scores are untouched.
    assert [r.score for r in out] == [r.score for r in recs]


def test_explore_zero_k_returns_the_ranked_prefix() -> None:
    recs = [
        _rec("a", 0.9, ("pop",)),
        _rec("b", 0.7, ("jazz",)),
        _rec("c", 0.5, ("folk",)),
    ]
    out = diversify(recs, 0.0, k=2)
    assert [r.artist.artist_id for r in out] == ["a", "b"]
    assert [r.rank for r in out] == [1, 2]


def test_higher_explore_raises_intra_list_tag_diversity() -> None:
    """Diversity of the top-k rises as ``explore`` increases toward 1."""
    recs = [
        _rec("a", 0.9, ("pop", "dance", "upbeat")),
        _rec("b", 0.85, ("pop", "dance", "energetic")),  # near-duplicate of a
        _rec("c", 0.5, ("jazz", "piano", "slow")),
        _rec("d", 0.3, ("folk", "acoustic", "quiet")),
    ]

    baseline = diversify(recs, 0.0, k=3)
    diversified = diversify(recs, 0.8, k=3)

    baseline_diversity = _mean_pairwise_jaccard_distance(baseline)
    diversified_diversity = _mean_pairwise_jaccard_distance(diversified)

    assert diversified_diversity >= baseline_diversity
    # Concretely: at explore=0 the two near-duplicate pop/dance artists (a, b)
    # both land in the top-3 (crowding out the diverse "d"); at high explore
    # the diversifier prefers pulling in a dissimilar candidate instead.
    assert {"a", "b"} <= {r.artist.artist_id for r in baseline}
    assert not ({"a", "b"} <= {r.artist.artist_id for r in diversified})


def test_first_pick_is_always_highest_relevance() -> None:
    """Regardless of ``explore``, the opening pick is the top-scoring candidate."""
    recs = [
        _rec("low", 0.2, ("rock",)),
        _rec("high", 0.95, ("rock",)),  # identical tags — no diversity available
        _rec("mid", 0.5, ("rock",)),
    ]
    for explore in (0.0, 0.3, 0.7, 1.0):
        out = diversify(recs, explore)
        assert out[0].artist.artist_id == "high"


# --- Invariant: permutation, never a re-score ------------------------------


@pytest.mark.parametrize("explore", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_output_is_a_permutation_never_a_rescore(explore: float) -> None:
    recs = [
        _rec("a", 0.9, ("pop", "dance")),
        _rec("b", 0.7, ("jazz", "piano")),
        _rec("c", 0.5, ("folk", "acoustic")),
        _rec("d", 0.5, ()),  # no tags — must never crash the similarity calc
    ]
    out = diversify(recs, explore)

    assert {r.artist.artist_id for r in out} == {r.artist.artist_id for r in recs}
    assert sorted(r.score for r in out) == sorted(r.score for r in recs)
    assert sorted(r.rank for r in out) == [1, 2, 3, 4]
    # base_score/rerank_delta are untouched — only rank moves.
    by_id = {r.artist.artist_id: r for r in recs}
    for r in out:
        original = by_id[r.artist.artist_id]
        assert r.base_score == original.base_score
        assert r.rerank_delta == original.rerank_delta


def test_k_limits_the_diversified_prefix() -> None:
    recs = [
        _rec("a", 0.9, ("pop",)),
        _rec("b", 0.7, ("jazz",)),
        _rec("c", 0.5, ("folk",)),
    ]
    out = diversify(recs, 0.5, k=2)
    assert len(out) == 2
    assert [r.rank for r in out] == [1, 2]


# --- Validation ---------------------------------------------------------------


@pytest.mark.parametrize("bad_explore", [-0.01, 1.01, -1.0, 2.0])
def test_explore_outside_unit_interval_raises(bad_explore: float) -> None:
    recs = [_rec("a", 0.5, ("pop",))]
    with pytest.raises(ValueError, match="explore"):
        diversify(recs, bad_explore)


def test_empty_input_returns_empty_list() -> None:
    assert diversify([], 0.5) == []
