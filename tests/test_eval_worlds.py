"""M3 acceptance: the hybrid beats a popularity baseline on held-out scrobbles."""

from __future__ import annotations

import pytest
from pipeline.lastfm import FixtureLastfm
from pipeline.models import Artist, Scrobble
from recommender.eval import (
    average_precision_at_k,
    eval_real,
    evaluate,
    evaluate_worlds,
    ground_truth,
    precision_recall_at_k,
    temporal_split,
    to_report,
)


def test_hybrid_beats_popularity_baseline(demo_user, scrobbles, catalog, source) -> None:
    report = to_report(evaluate(demo_user, scrobbles, catalog, source, k=5))
    assert report["hybrid_beats_popularity"] is True
    assert report["models"]["hybrid"]["map_at_k"] > report["models"]["popularity"]["map_at_k"]
    assert (
        report["models"]["hybrid"]["recall_at_k"] >= report["models"]["popularity"]["recall_at_k"]
    )


def test_temporal_split_is_chronological(scrobbles) -> None:
    train, test = temporal_split(scrobbles, 0.7)
    assert train and test
    assert max(s.ts for s in train) <= min(s.ts for s in test)


def test_ground_truth_is_test_only_discoveries(scrobbles) -> None:
    train, test = temporal_split(scrobbles, 0.7)
    gt = ground_truth(train, test)
    train_ids = {s.artist_id for s in train}
    assert gt
    assert not (gt & train_ids)


def test_precision_recall_math() -> None:
    ranked = ["a", "b", "c", "d"]
    positives = {"a", "c", "z"}
    p, r = precision_recall_at_k(ranked, positives, k=4)
    assert p == 0.5  # 2 of 4
    assert r == pytest.approx(2 / 3)


def test_average_precision_math() -> None:
    ranked = ["a", "x", "b"]
    positives = {"a", "b"}
    # hits at ranks 1 and 3 → (1/1 + 2/3) / 2
    assert average_precision_at_k(ranked, positives, k=3) == pytest.approx((1 + 2 / 3) / 2)


def test_empty_positives_score_zero() -> None:
    assert average_precision_at_k(["a"], set(), k=1) == 0.0
    assert precision_recall_at_k([], {"a"}, k=5) == (0.0, 0.0)


def test_split_rejects_bad_fraction(scrobbles) -> None:
    with pytest.raises(ValueError):
        temporal_split(scrobbles, 1.0)


# --- FIX-06: effect sizes + multi-world aggregation + the real-data leg -----


def test_to_report_includes_effect_sizes(demo_user, scrobbles, catalog, source) -> None:
    report = to_report(evaluate(demo_user, scrobbles, catalog, source, k=5))
    hybrid_map = report["models"]["hybrid"]["map_at_k"]
    pop_map = report["models"]["popularity"]["map_at_k"]
    hybrid_recall = report["models"]["hybrid"]["recall_at_k"]
    pop_recall = report["models"]["popularity"]["recall_at_k"]
    assert report["map_delta"] == pytest.approx(hybrid_map - pop_map)
    assert report["recall_delta"] == pytest.approx(hybrid_recall - pop_recall)
    assert report["lift"] is not None
    assert report["verdict"] == "hybrid"
    assert report["hybrid_beats_popularity"] is True  # back-compat field retained


def test_evaluate_worlds_aggregates_at_least_four_worlds_with_caveats() -> None:
    report = evaluate_worlds(k=5)
    assert report["n_worlds"] >= 4
    assert report["caveats"]  # the tuning caveat is documented in the report itself
    assert "demo-tuned-indie" in report["caveats"]
    assert 0 <= report["worlds_hybrid_wins"] <= report["n_worlds"]

    worlds = report["worlds"]
    assert set(worlds) == {
        "demo-tuned-indie",
        "sparse-tags",
        "popularity-skewed",
        "no-collaborative-signal",
        "adversarial-near-misses",
    }
    for name, world_report in worlds.items():
        assert "map_delta" in world_report, name
        assert "recall_delta" in world_report, name
        assert "lift" in world_report, name
        assert "verdict" in world_report, name
        assert "hybrid_beats_popularity" in world_report, name


def test_compare_lift_undefined_when_baseline_map_is_zero() -> None:
    from recommender.eval import EvalResult, compare

    hybrid = EvalResult(
        model="hybrid", k=5, precision_at_k=0.4, recall_at_k=0.5, map_at_k=0.3, n_positives=2
    )
    popularity = EvalResult(
        model="popularity", k=5, precision_at_k=0.0, recall_at_k=0.0, map_at_k=0.0, n_positives=2
    )
    comparison = compare({"hybrid": hybrid, "popularity": popularity})
    assert comparison.lift is None  # unbounded/undefined, never Infinity
    assert comparison.hybrid_beats_popularity is True
    assert comparison.verdict == "hybrid"


def test_compare_lift_is_one_when_both_zero() -> None:
    from recommender.eval import EvalResult, compare

    zero = EvalResult(
        model="x", k=5, precision_at_k=0.0, recall_at_k=0.0, map_at_k=0.0, n_positives=2
    )
    comparison = compare({"hybrid": zero, "popularity": zero})
    assert comparison.lift == 1.0
    # A draw is not a win. Two models scoring an identical 0.0 on every metric
    # used to return True here, which made "the offline eval must beat the
    # popularity baseline" (README) a gate that a total tie satisfied.
    assert comparison.hybrid_beats_popularity is False
    assert comparison.verdict == "popularity"


def test_evaluate_worlds_aggregate_can_lose(demo_user) -> None:
    """A deliberately signal-free world where popularity wins outright, so the
    aggregate verdict can honestly be 'popularity' — the gate is not rigged to
    always pass regardless of the numbers.
    """
    seeds_scrobbles = [Scrobble("x1", "X1", "t", 1_700_000_000 + i * 3600) for i in range(5)] + [
        Scrobble("x2", "X2", "t", 1_700_020_000 + i * 3600) for i in range(5)
    ]
    discovery_scrobbles = [Scrobble("d1", "D1", "t", 1_700_100_000 + i * 3600) for i in range(2)]
    scrobbles = seeds_scrobbles + discovery_scrobbles
    catalog = {
        "x1": Artist(artist_id="x1", name="X1", tags=(), listeners=0),
        "x2": Artist(artist_id="x2", name="X2", tags=(), listeners=0),
        "d1": Artist(artist_id="d1", name="D1", tags=(), listeners=1000),
        "c1": Artist(artist_id="c1", name="C1", tags=(), listeners=0),
        "c2": Artist(artist_id="c2", name="C2", tags=(), listeners=0),
    }
    # No tags anywhere and no similarity edges: the hybrid has zero signal and
    # falls back to alphabetical tie-break, which does not favor "d1".
    source = FixtureLastfm(scrobbles={demo_user: scrobbles}, tags={}, similar={})

    def losing_world():
        return demo_user, scrobbles, catalog, source

    report = evaluate_worlds({"signal-free": losing_world}, k=2)
    assert report["worlds"]["signal-free"]["verdict"] == "popularity"
    assert report["hybrid_beats_popularity"] is False
    assert report["verdict"] == "popularity"
    assert report["worlds_hybrid_wins"] == 0


def test_evaluate_worlds_accepts_a_custom_world_dict() -> None:
    # A trivial one-world dict, reusing the demo fixtures, exercises the
    # non-default code path without depending on pipeline.fixtures internals.
    from pipeline.demo import DEMO_USER, demo_catalog, demo_scrobbles, demo_source

    worlds = {"solo": lambda: (DEMO_USER, demo_scrobbles(), demo_catalog(), demo_source())}
    report = evaluate_worlds(worlds, k=5)
    assert report["n_worlds"] == 1
    assert "solo" in report["worlds"]


def test_eval_real_rejects_missing_scrobbles(tmp_path, catalog, source) -> None:
    from pipeline.cache import Cache

    db_path = tmp_path / "empty-cache.db"
    with Cache(db_path):
        pass  # creates the schema; no scrobbles are ever inserted

    with pytest.raises(ValueError):
        eval_real("nobody", db_path, catalog, source)


def test_eval_real_returns_only_a_summary_never_raw_plays(
    tmp_path, demo_user, scrobbles, catalog, source
) -> None:
    from pipeline.cache import Cache

    db_path = tmp_path / "cache.db"
    with Cache(db_path) as cache:
        cache.put_scrobbles(demo_user, scrobbles)

    report = eval_real(demo_user, db_path, catalog, source, k=5, today="2026-07-03")

    assert report["date"] == "2026-07-03"
    assert report["n"] == len(scrobbles)
    # Only summarized metrics — never the raw scrobble list.
    assert set(report) == {
        "date",
        "n",
        "k",
        "n_positives",
        "map_delta",
        "recall_delta",
        "lift",
        "hybrid_beats_popularity",
        "verdict",
        "models",
    }


# --- #82: a metric that cannot fail is not a measurement ---------------------


def test_recall_is_marked_non_discriminating_when_k_covers_the_whole_pool() -> None:
    """With k >= pool size the top-k is the pool, so recall is the same for any order.

    The check is deliberately behavioral rather than a bare flag assertion: it
    scores a perfect ranking and its exact reverse and shows they tie.
    """
    from recommender.eval import _score

    positives = {"a", "c"}
    best = ["a", "c", "b", "d"]
    worst = list(reversed(best))

    perfect = _score("perfect", best, positives, k=5)
    reversed_ = _score("reversed", worst, positives, k=5)

    assert perfect.recall_at_k == reversed_.recall_at_k == 1.0
    assert perfect.recall_discriminates is False
    assert reversed_.recall_discriminates is False
    # MAP still separates them, which is why it is the metric the verdict uses.
    assert perfect.map_at_k > reversed_.map_at_k


def test_recall_discriminates_once_k_is_below_the_pool() -> None:
    from recommender.eval import _score

    positives = {"a", "c"}
    best = ["a", "c", "b", "d"]
    worst = list(reversed(best))

    perfect = _score("perfect", best, positives, k=2)
    reversed_ = _score("reversed", worst, positives, k=2)

    assert perfect.recall_discriminates is True
    assert perfect.recall_at_k > reversed_.recall_at_k


def test_mean_recall_delta_excludes_the_worlds_where_recall_could_not_vary() -> None:
    """The headline number must not be one world's value divided by five.

    Before this, four of the five shipped worlds had a four-candidate pool at
    k=5, contributed a structurally-pinned recall_delta of 0.0, and turned
    demo-tuned-indie's 0.5 into a reported `mean_recall_delta: 0.1`.
    """
    report = evaluate_worlds(k=5)

    pinned = report["recall_pinned_worlds"]
    n_discriminating = report["n_worlds_recall_discriminating"]
    assert n_discriminating == report["n_worlds"] - len(pinned)

    contributing = [
        w["recall_delta"] for w in report["worlds"].values() if w["recall_discriminates"]
    ]
    assert len(contributing) == n_discriminating
    if contributing:
        expected = round(sum(contributing) / len(contributing), 4)
        assert report["mean_recall_delta"] == expected
    else:
        assert report["mean_recall_delta"] is None


def test_the_denominator_travels_with_the_headline_recall_number() -> None:
    """A caveat that stays in a docstring is not a caveat on the number."""
    report = evaluate_worlds(k=5)
    if report["recall_pinned_worlds"]:
        assert "recall_caveat" in report
        assert "n_worlds_recall_discriminating" in report
        for name in report["recall_pinned_worlds"]:
            assert name in report["recall_caveat"]
            assert report["worlds"][name]["recall_discriminates"] is False
            assert "recall_note" in report["worlds"][name]


def test_a_world_where_recall_can_vary_carries_no_pinned_note() -> None:
    report = evaluate_worlds(k=5)
    discriminating = {name for name, w in report["worlds"].items() if w["recall_discriminates"]}
    assert discriminating, "at least one world must be able to measure recall"
    for name in discriminating:
        assert "recall_note" not in report["worlds"][name]


def test_an_exact_draw_across_every_world_does_not_pass_the_gate() -> None:
    """The aggregate rule used to accept `map_delta == 0 and recall_delta >= 0`.

    A world where the hybrid and the baseline return identical rankings hits
    that clause exactly, and the multiworld gate reported verdict "hybrid".
    """
    tied = _tied_world()
    report = evaluate_worlds({"tied": tied}, k=5)
    assert report["mean_map_delta"] == 0.0
    assert report["hybrid_beats_popularity"] is False
    assert report["verdict"] == "popularity"


def _tied_world():
    """A world with one candidate, so every ranker returns the same list."""

    def build():
        catalog = {
            "seed": Artist(artist_id="seed", name="Seed", tags=("t",), listeners=100),
            "only": Artist(artist_id="only", name="Only", tags=("t",), listeners=50),
        }
        scrobbles = [Scrobble("seed", "Seed", "s1", 1_700_000_000 + i * 3600) for i in range(7)]
        scrobbles += [Scrobble("only", "Only", "o1", 1_700_100_000 + i * 3600) for i in range(3)]
        source = FixtureLastfm(
            scrobbles={"u": scrobbles}, tags={"seed": ("t",), "only": ("t",)}, similar={}
        )
        return "u", scrobbles, catalog, source

    return build


# --- #82: a metric that cannot vary must not be reported as evidence ---------
#
# The reporting half of #82 already landed: `EvalResult.recall_discriminates`,
# the `recall_note`/`recall_caveat` text, `n_worlds_recall_discriminating`, and
# an aggregate verdict that requires a strict MAP improvement. The fixture half
# had not. Every non-demo world held exactly four candidates against `k=5`, so
# the top-k was the whole pool for *any* ordering and `recall_at_k` came out 1.0
# for the hybrid, the popularity baseline, a random ranker and a reversed one
# alike. Four of five worlds could not contribute a recall signal and never
# could have, which is not a property of the ranker but of the fixture.
#
# These assertions are the standing guard. Shrinking a world back below `k`
# fails here rather than quietly re-pinning the metric and reporting it.

EVAL_K = 5  # what `make eval` passes (see the Makefile's `eval` target)


def _rankable_pool(build) -> int:
    """How many candidates a world actually gives a ranker to order."""
    from pipeline.ingest import build_profile
    from recommender.hybrid import recommend

    username, scrobbles, catalog, world_source = build()
    train, _ = temporal_split(scrobbles, 0.7)
    profile = build_profile(username, train)
    return len(recommend(profile, catalog, world_source, k=10_000))


def test_every_world_gives_the_ranker_more_candidates_than_k() -> None:
    from pipeline.fixtures import ALL_WORLDS

    too_small = {
        name: pool
        for name, build in ALL_WORLDS.items()
        if (pool := _rankable_pool(build)) <= EVAL_K
    }
    assert not too_small, (
        "recall_at_k cannot discriminate in these worlds — the top-k is the whole "
        f"rankable pool, so every possible ranking scores the same: {too_small}. "
        f"Widen the candidate pool past k={EVAL_K} (#82)."
    )


def test_no_world_is_excluded_from_the_recall_aggregate() -> None:
    """The report's own honesty machinery must now have nothing to report."""
    aggregate = evaluate_worlds(k=EVAL_K)

    assert aggregate["recall_pinned_worlds"] == []
    assert aggregate["n_worlds_recall_discriminating"] == aggregate["n_worlds"]
    assert "recall_caveat" not in aggregate
    for name, world in aggregate["worlds"].items():
        assert world["recall_discriminates"] is True, name
        assert "recall_note" not in world, name


def test_the_recall_mean_is_over_every_world_now() -> None:
    """`mean_recall_delta` was one world's number divided by five."""
    aggregate = evaluate_worlds(k=EVAL_K)
    deltas = [w["recall_delta"] for w in aggregate["worlds"].values()]

    assert aggregate["mean_recall_delta"] == pytest.approx(sum(deltas) / len(deltas))
    assert len(deltas) == aggregate["n_worlds"]


def test_the_pinned_worlds_machinery_still_works_when_k_is_too_big() -> None:
    """The guard above is only meaningful if the detector it guards still fires.

    Run the same worlds at a `k` larger than any pool: every world must be
    reported as non-discriminating, the caveat must come back, and the recall
    mean must be `None` rather than a number averaged out of nothing.
    """
    aggregate = evaluate_worlds(k=10_000)

    assert set(aggregate["recall_pinned_worlds"]) == set(aggregate["worlds"])
    assert aggregate["n_worlds_recall_discriminating"] == 0
    assert aggregate["mean_recall_delta"] is None
    assert "recall_caveat" in aggregate


def test_a_draw_does_not_pass_the_beat_the_baseline_gate() -> None:
    """README calls this gate "the offline eval must beat the popularity baseline".

    Asserted rather than assumed: an aggregate rule of `mean_map_delta >= 0`
    would report a dead heat as a win, and the gate would be one that a ranker
    doing nothing at all could pass.
    """
    from recommender.eval import EvalResult, compare

    tie = EvalResult(
        model="hybrid",
        k=5,
        precision_at_k=0.4,
        recall_at_k=0.5,
        map_at_k=0.5,
        n_positives=2,
        n_ranked=8,
    )
    same = EvalResult(
        model="popularity",
        k=5,
        precision_at_k=0.4,
        recall_at_k=0.5,
        map_at_k=0.5,
        n_positives=2,
        n_ranked=8,
    )
    result = compare({"hybrid": tie, "popularity": same})

    assert result.hybrid_beats_popularity is False
    assert result.verdict == "popularity"
