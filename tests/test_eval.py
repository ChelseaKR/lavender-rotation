"""M3 acceptance: the hybrid beats a popularity baseline on held-out scrobbles."""

from __future__ import annotations

import pytest
from recommender.eval import (
    EvalResult,
    average_precision_at_k,
    check_regression,
    evaluate,
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


def _result(**overrides: float) -> EvalResult:
    base = {
        "model": "hybrid",
        "k": 5,
        "precision_at_k": 0.6,
        "recall_at_k": 0.75,
        "map_at_k": 0.6875,
        "n_positives": 4,
    }
    base.update(overrides)
    return EvalResult(**base)  # type: ignore[arg-type]


def test_check_regression_passes_at_baseline() -> None:
    baseline = {"precision_at_k": 0.6, "recall_at_k": 0.75, "map_at_k": 0.6875}
    result = check_regression(_result(), baseline)
    assert result["regressed"] is False


def test_check_regression_flags_a_real_drop() -> None:
    baseline = {"precision_at_k": 0.6, "recall_at_k": 0.75, "map_at_k": 0.6875}
    # map_at_k drops well past the default 10% tolerance floor (0.6188).
    result = check_regression(_result(map_at_k=0.4), baseline)
    assert result["regressed"] is True
    assert result["metrics"]["map_at_k"]["regressed"] is True
    assert result["metrics"]["precision_at_k"]["regressed"] is False


def test_check_regression_tolerates_small_drops() -> None:
    baseline = {"map_at_k": 0.6875}
    # a 5% drop is within the default 10% tolerance.
    result = check_regression(_result(map_at_k=0.6875 * 0.95), baseline)
    assert result["regressed"] is False


def test_check_regression_skips_metrics_absent_from_baseline() -> None:
    result = check_regression(_result(precision_at_k=0.0), {"map_at_k": 0.6875})
    assert result["regressed"] is False
    assert "precision_at_k" not in result["metrics"]


# -- the `lavender eval` gate end-to-end (exit codes are the merge-blocking contract) --


def _run_eval(tmp_path, baseline: str | None) -> tuple[int, dict]:
    import json

    from pipeline.cli import main as cli_main

    out = tmp_path / "eval-report.json"
    argv = ["eval", "--k", "5", "--out", str(out)]
    if baseline is not None:
        argv += ["--baseline", baseline]
    code = cli_main(argv)
    return code, json.loads(out.read_text(encoding="utf-8"))


def test_cli_eval_passes_against_the_committed_baseline(tmp_path, capsys) -> None:
    code, report = _run_eval(tmp_path, "docs/audits/eval-baseline.json")
    assert code == 0
    assert report["hybrid_beats_popularity"] is True
    assert report["regression_vs_baseline"]["regressed"] is False
    assert report["fairness"]["guarantees"]["unknown_retention_all_lenses"] is True


def test_cli_eval_missing_baseline_warns_but_passes(tmp_path, capsys) -> None:
    code, report = _run_eval(tmp_path, str(tmp_path / "no-such-baseline.json"))
    assert code == 0  # a fresh clone without a baseline must still pass
    assert "skipping regression check" in capsys.readouterr().err
    assert "regression_vs_baseline" not in report
    assert "fairness" in report  # fairness block is emitted regardless


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "{}",
        '{"metrics": []}',
        '{"metrics": {"map_at_k": true}}',
        '{"metrics": {"map_at_k": NaN}}',
        '{"metrics": {"map_at_k": 0.5}}',
        '{"metrics": {"map_at_typo": 0.5}}',
        '{"metrics": {"precision_at_k": 0.5, "recall_at_k": 0.5, "map_at_k": 0.5}, '
        '"tolerance": 1.0}',
    ],
)
def test_cli_eval_rejects_malformed_baseline_cleanly(tmp_path, capsys, payload: str) -> None:
    from pipeline.cli import main as cli_main

    baseline = tmp_path / "bad-baseline.json"
    baseline.write_text(payload, encoding="utf-8")
    out = tmp_path / "eval-report.json"

    assert cli_main(["eval", "--out", str(out), "--baseline", str(baseline)]) == 2
    assert "invalid eval baseline" in capsys.readouterr().err
    assert not out.exists()


def test_cli_eval_names_every_missing_baseline_metric(tmp_path, capsys) -> None:
    from pipeline.cli import main as cli_main

    baseline = tmp_path / "partial-baseline.json"
    baseline.write_text('{"metrics": {"map_at_k": 0.5}}', encoding="utf-8")

    assert cli_main(["eval", "--baseline", str(baseline)]) == 2
    error = capsys.readouterr().err
    assert "missing metric(s)" in error
    assert "precision_at_k" in error and "recall_at_k" in error


@pytest.mark.parametrize("command", ["eval", "recommend", "export", "report"])
def test_cli_rejects_nonpositive_k(command: str) -> None:
    from pipeline.cli import main as cli_main

    with pytest.raises(SystemExit) as exc:
        cli_main([command, "--k", "0"])
    assert exc.value.code == 2


def test_cli_eval_fails_on_regression_vs_baseline(tmp_path, capsys) -> None:
    import json

    impossible = {
        "metrics": {
            "precision_at_k": 100.0,
            "recall_at_k": 100.0,
            "map_at_k": 100.0,
        },
        "tolerance": 0.10,
    }
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(impossible), encoding="utf-8")
    code, report = _run_eval(tmp_path, str(baseline))
    assert code == 1
    assert report["regression_vs_baseline"]["regressed"] is True
    assert "regressed vs docs/audits/eval-baseline.json" in capsys.readouterr().err


# --- CLI numeric arguments that were not validated ---------------------------
#
# `--k` goes through `_positive_int`, `--ttl-days` through `_nonnegative_int`,
# `--explore` was range-checked deep inside `recommender.diversify` — and
# `--lens` was checked nowhere. `--lens 5`, `--lens -1`, `--lens nan` and
# `--lens inf` all parsed, then surfaced as a bare ValueError traceback out of
# the ranker (and, for `report`, out of a dict comprehension) instead of
# argparse's clean usage error and exit 2.


@pytest.mark.parametrize("command", ["recommend", "export", "report"])
@pytest.mark.parametrize("value", ["5", "-1", "nan", "inf", "-inf", "banana"])
def test_lens_strength_outside_the_unit_interval_is_an_argparse_error(
    command: str, value: str
) -> None:
    from pipeline.cli import main

    with pytest.raises(SystemExit) as exc:
        main([command, "--lens", value])
    assert exc.value.code == 2, "a bad flag must exit 2, not raise from inside the ranker"


@pytest.mark.parametrize("value", ["5", "-1", "nan", "inf"])
def test_explore_outside_the_unit_interval_is_an_argparse_error(value: str) -> None:
    from pipeline.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["recommend", "--explore", value])
    assert exc.value.code == 2


@pytest.mark.parametrize("value", ["0", "0.5", "1", "1.0"])
def test_the_whole_unit_interval_is_still_accepted(value: str) -> None:
    """The bound must not be "reject everything", which would also always pass."""
    from pipeline.cli import _unit_interval

    assert 0.0 <= _unit_interval(value) <= 1.0


def test_map_at_k_refuses_a_non_positive_k() -> None:
    """It divided by `min(len(positives), k)`: ZeroDivisionError at 0, negative MAP below.

    `precision_recall_at_k` short-circuits on an empty top-k and this one did
    not, so the two halves of the same metric pair disagreed about what k=0
    means. The CLI never passes one, but `evaluate`/`evaluate_worlds`/
    `fairness_report`/`eval_real` are public API.
    """
    from recommender.eval import average_precision_at_k

    for bad in (0, -1, -5):
        with pytest.raises(ValueError, match="k must be positive"):
            average_precision_at_k(["a", "b"], {"a"}, bad)
    assert average_precision_at_k(["a", "b"], {"a"}, 1) == 1.0
