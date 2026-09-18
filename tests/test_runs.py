"""Run manifests, and a diff that names a cause only where the record supports one."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from pipeline.cli import main
from pipeline.runs import (
    CAUSE_EXPLORE,
    CAUSE_FEEDBACK,
    CAUSE_FILTER,
    CAUSE_LENS,
    CAUSE_PROFILE,
    CAUSE_UNDETERMINED,
    CAUSE_UPSTREAM,
    MAX_MANIFEST_BYTES,
    RUN_MANIFEST_SCHEMA_VERSION,
    RunEntry,
    RunManifest,
    RunManifestError,
    attribute_shift,
    diff_runs,
    find_manifest,
    list_manifest_paths,
    prune_manifests,
    read_manifest,
    runs_dir,
    write_manifest,
)


def _entry(artist_id: str, rank: int, base: int, lens: int, segment: str = "woman") -> RunEntry:
    return RunEntry(
        artist_id=artist_id,
        name=artist_id.title(),
        rank=rank,
        base_rank=base,
        lens_rank=lens,
        segment=segment,
        basis="self-identified",
    )


def _manifest(run_id: str, entries: list[RunEntry], **overrides: object) -> RunManifest:
    payload: dict[str, object] = {
        "run_id": run_id,
        "created_at": f"2026-09-0{run_id[-1]}T00:00:00+00:00",
        "surface": "recommend",
        "listener_digest": "listener00000000",
        "profile_digest": "profile00000000",
        "feedback_digest": "feedback0000000",
        "lens_name": "women-nonbinary",
        "lens_strength": 0.0,
        "explore": 0.0,
        "hide_sourced_men": False,
        "k": 10,
        "content_filter": {"stated": True, "active": False},
        "cache_schema_version": 4,
        "coverage": {"total": len(entries), "sourced": len(entries), "sourced_fraction": 1.0},
        "exposure": {"woman": 1.0, "unknown": 0.0},
        "entries": entries,
    }
    payload.update(overrides)
    return RunManifest(**payload)  # type: ignore[arg-type]


# --- Attribution: the whole feature, and the easy thing to get wrong -----


def test_a_shift_in_one_interval_alone_is_attributed_to_that_mechanism() -> None:
    # Only the lens interval moved: base_rank is unchanged and the
    # rank-minus-lens_rank gap is unchanged, so the lens is the only mechanism
    # the record shows moving.
    before = _entry("a", rank=5, base=5, lens=5)
    after = _entry("a", rank=2, base=5, lens=2)
    assert attribute_shift(before, after, []) == (CAUSE_LENS, [CAUSE_LENS])

    # Only the serendipity/hide gap moved.
    before = _entry("b", rank=4, base=4, lens=4)
    after = _entry("b", rank=6, base=4, lens=4)
    assert attribute_shift(before, after, []) == (CAUSE_EXPLORE, [CAUSE_EXPLORE])


def test_two_intervals_moving_is_not_determined_rather_than_the_first_one_checked() -> None:
    # This is the case the issue's Done-when does not cover and the one that
    # occurs most in real use: change the lens and add a thumbs-down, and no
    # record can separate their contributions. A diff that attributes every
    # movement to something is the #113 defect wearing a different hat.
    # Written first with `after` at rank 1, which failed: that also moved the
    # explore interval, so all three moved rather than two. Keeping
    # `rank - lens_rank` fixed at zero is what isolates the pair.
    before = _entry("a", rank=5, base=5, lens=5)
    after = _entry("a", rank=2, base=3, lens=2)
    cause, candidates = attribute_shift(before, after, [CAUSE_FEEDBACK])
    assert cause == CAUSE_UNDETERMINED
    assert set(candidates) == {CAUSE_UPSTREAM, CAUSE_LENS}

    # And all three at once is still undetermined, with all three named.
    everything = _entry("a", rank=1, base=3, lens=2)
    cause, candidates = attribute_shift(before, everything, [CAUSE_FEEDBACK])
    assert cause == CAUSE_UNDETERMINED
    assert set(candidates) == {CAUSE_UPSTREAM, CAUSE_LENS, CAUSE_EXPLORE}


def test_an_upstream_shift_names_the_one_recorded_input_that_changed() -> None:
    before = _entry("a", rank=4, base=4, lens=4)
    after = _entry("a", rank=2, base=2, lens=2)
    assert attribute_shift(before, after, [CAUSE_PROFILE]) == (CAUSE_PROFILE, [CAUSE_PROFILE])
    assert attribute_shift(before, after, [CAUSE_FILTER]) == (CAUSE_FILTER, [CAUSE_FILTER])


def test_two_upstream_inputs_changing_is_not_determined_either() -> None:
    before = _entry("a", rank=4, base=4, lens=4)
    after = _entry("a", rank=2, base=2, lens=2)
    cause, candidates = attribute_shift(before, after, [CAUSE_PROFILE, CAUSE_FEEDBACK])
    assert cause == CAUSE_UNDETERMINED
    assert candidates == [CAUSE_PROFILE, CAUSE_FEEDBACK]


def test_a_base_shift_with_nothing_recorded_changing_is_not_determined() -> None:
    # The failure mode a shrug dressed as an answer would hide: the pure-taste
    # ordering moved and none of the three inputs the manifest records did. The
    # honest reading is that something outside the record moved it.
    before = _entry("a", rank=4, base=4, lens=4)
    after = _entry("a", rank=2, base=2, lens=2)
    cause, candidates = attribute_shift(before, after, [])
    assert cause == CAUSE_UNDETERMINED
    assert candidates == [CAUSE_UPSTREAM]


def test_changing_only_the_lens_attributes_every_shift_to_it_and_moves_no_unknown() -> None:
    # The issue's second Done-when. Unknown artists are rank-protected, so their
    # three ranks move together and they are not in the shift list at all.
    before = _manifest(
        "1",
        [
            _entry("known", rank=1, base=1, lens=1),
            _entry("mover", rank=3, base=3, lens=3),
            _entry("unknown-act", rank=2, base=2, lens=2, segment="unknown"),
        ],
    )
    after = _manifest(
        "2",
        [
            _entry("known", rank=1, base=1, lens=1),
            _entry("mover", rank=2, base=3, lens=2),
            _entry("unknown-act", rank=3, base=2, lens=3, segment="unknown"),
        ],
        lens_strength=1.0,
    )
    result = diff_runs(before, after)
    assert {shift.artist_id for shift in result.shifts} == {"mover", "unknown-act"}
    assert all(shift.cause == CAUSE_LENS for shift in result.shifts)
    unknown_shift = next(s for s in result.shifts if s.artist_id == "unknown-act")
    # Its displacement is the lens moving the artist above it, which is what the
    # record shows; the point of the test is that no *cause* is invented for it.
    assert unknown_shift.cause == CAUSE_LENS


# --- Two identical runs -------------------------------------------------


def test_two_runs_with_the_same_inputs_diff_as_no_changes() -> None:
    # The issue's first Done-when, over the manifest rather than the terminal.
    entries = [_entry("a", 1, 1, 1), _entry("b", 2, 2, 2)]
    before = _manifest("1", entries)
    after = _manifest("2", list(entries))
    result = diff_runs(before, after)
    assert result.unchanged
    assert result.held == 2
    assert result.entered == [] and result.left == []
    assert before.profile_digest == after.profile_digest
    assert "no changes" in "\n".join(result.summary_lines())


def test_artists_entering_and_leaving_are_reported_separately_from_shifts() -> None:
    before = _manifest("1", [_entry("a", 1, 1, 1), _entry("gone", 2, 2, 2)])
    after = _manifest("2", [_entry("a", 1, 1, 1), _entry("new", 2, 2, 2)])
    result = diff_runs(before, after)
    assert [e.artist_id for e in result.entered] == ["new"]
    assert [e.artist_id for e in result.left] == ["gone"]
    assert result.shifts == []
    assert result.held == 1


# --- Absence stays absent ----------------------------------------------


def test_a_delta_against_an_unmeasured_figure_is_none_not_zero() -> None:
    # Both `IdentityCoverage.to_dict` and `exposure_at_k` publish None where a
    # figure was not measurable. Subtracting them into 0.0 would put the absence
    # back as a number in the one document written to be cited.
    before = _manifest(
        "1",
        [_entry("a", 1, 1, 1)],
        coverage={"total": 0, "sourced_fraction": None},
        exposure={"woman": None, "unknown": None},
    )
    after = _manifest(
        "2",
        [_entry("a", 1, 1, 1)],
        coverage={"total": 1, "sourced_fraction": 1.0},
        exposure={"woman": 1.0, "unknown": 0.0},
    )
    result = diff_runs(before, after)
    assert result.coverage_delta["total"] == 1
    assert result.coverage_delta["sourced_fraction"] is None
    assert result.exposure_delta["woman"] is None
    assert result.exposure_delta["unknown"] is None
    # And a real zero survives as a real zero.
    both_measured = diff_runs(_manifest("1", []), _manifest("2", []))
    assert both_measured.exposure_delta["woman"] == 0.0


# --- Runs that answer different questions -------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("listener_digest", "someone-else"),
        ("lens_name", "queer"),
        ("content_filter", {"stated": True, "active": True, "include_tags": ["punk"]}),
    ],
)
def test_a_diff_across_a_different_question_is_refused_by_default(
    field: str, value: object
) -> None:
    before = _manifest("1", [_entry("a", 1, 1, 1)])
    after = _manifest("2", [_entry("a", 1, 1, 1)], **{field: value})
    with pytest.raises(RunManifestError, match=field):
        diff_runs(before, after)
    allowed = diff_runs(before, after, allow_mixed=True)
    assert allowed.mixed_fields == [field]
    assert "different questions" in "\n".join(allowed.summary_lines())


# --- Reading a manifest -------------------------------------------------


def test_an_unknown_schema_version_is_refused_and_named(tmp_path: Path) -> None:
    # The issue's fourth Done-when. Named rather than merely rejected: a reader
    # told only "unsupported" has to open the file to learn what it was.
    path = tmp_path / "20260101T000000-recommend.json"
    payload = _manifest("1", [_entry("a", 1, 1, 1)]).to_dict()
    payload["schema_version"] = "9.9"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RunManifestError, match=r"9\.9"):
        read_manifest(path)


def test_a_malformed_manifest_is_refused_rather_than_partially_read(tmp_path: Path) -> None:
    path = tmp_path / "20260101T000000-recommend.json"
    payload = _manifest("1", [_entry("a", 1, 1, 1)]).to_dict()
    del payload["entries"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RunManifestError, match="malformed"):
        read_manifest(path)


def test_an_oversized_file_is_refused_without_parsing_it(tmp_path: Path) -> None:
    path = tmp_path / "20260101T000000-recommend.json"
    path.write_bytes(b"[" + b"0," * (MAX_MANIFEST_BYTES // 2) + b"0]")
    with pytest.raises(RunManifestError, match="refusing to read"):
        read_manifest(path)


def test_the_schema_version_is_its_own_literal() -> None:
    # A property test cannot catch a version that silently changed, and the
    # version is what a reader of an old manifest is told.
    assert RUN_MANIFEST_SCHEMA_VERSION == "1.0"
    assert _manifest("1", []).to_dict()["schema_version"] == "1.0"


# --- The manifest store -------------------------------------------------


def test_manifests_round_trip_and_prune_keeps_the_newest(tmp_path: Path) -> None:
    for index in range(5):
        write_manifest(_manifest(f"2026010{index}T000000", []), data_dir=tmp_path)
    assert len(list_manifest_paths(tmp_path)) == 5
    removed = prune_manifests(keep=2, data_dir=tmp_path)
    assert len(removed) == 3
    kept = [read_manifest(p).run_id for p in list_manifest_paths(tmp_path)]
    assert kept == ["20260103T000000", "20260104T000000"]


def test_a_run_id_prefix_resolves_and_an_ambiguous_one_is_refused(tmp_path: Path) -> None:
    write_manifest(_manifest("20260101T000000", []), data_dir=tmp_path)
    write_manifest(_manifest("20260101T000001", []), data_dir=tmp_path)
    assert find_manifest("20260101T000000", tmp_path).name.startswith("20260101T000000")
    with pytest.raises(RunManifestError, match="more than one"):
        find_manifest("202601", tmp_path)
    with pytest.raises(RunManifestError, match="no run manifest matches"):
        find_manifest("nope", tmp_path)


# --- Privacy ------------------------------------------------------------


def test_a_manifest_holds_no_listening_history_and_no_inferred_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The guardrail restated for a new artifact: the manifest is a document that
    # may be kept and shown, so it must carry no play counts and no slot an
    # inferred identity could occupy.
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    assert main(["recommend", "--k", "5"]) == 0
    paths = list_manifest_paths(tmp_path)
    assert paths, "recommend should have recorded a manifest"
    raw = paths[-1].read_text(encoding="utf-8")
    payload = json.loads(raw)
    for forbidden in ("play_count", "play_counts", "scrobble", "plays", "inferred", "guess"):
        assert forbidden not in raw
    entry_keys = set(payload["entries"][0])
    assert entry_keys == {
        "artist_id",
        "name",
        "rank",
        "base_rank",
        "lens_rank",
        "segment",
        "basis",
    }
    # The listener appears only as a digest, and the digest is not the username.
    assert "demo" not in payload["listener_digest"]


def test_the_recorded_run_reflects_the_flags_that_were_actually_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    assert main(["recommend", "--k", "4", "--lens", "1", "--explore", "0.5"]) == 0
    manifest = read_manifest(list_manifest_paths(tmp_path)[-1])
    assert manifest.surface == "recommend"
    assert manifest.k == 4
    assert manifest.lens_strength == 1.0
    assert manifest.explore == 0.5
    assert manifest.cache_schema_version >= 1


def test_two_identical_cli_runs_produce_identical_digests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The issue's first Done-when, end to end. The run ids differ because they
    # are timestamps; everything that describes the *question* must not.
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    assert main(["recommend", "--k", "5"]) == 0
    assert main(["recommend", "--k", "5"]) == 0
    first, second = (read_manifest(p) for p in list_manifest_paths(tmp_path))
    assert first.run_id != second.run_id
    assert first.profile_digest == second.profile_digest
    assert first.feedback_digest == second.feedback_digest
    assert first.listener_digest == second.listener_digest
    assert diff_runs(first, second).unchanged


def test_a_manifest_that_cannot_be_written_does_not_fail_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Recording is observability, not the product. A read-only data directory
    # must not turn a working recommendation into an error.
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr("pipeline.cli.write_manifest", refuse)
    assert main(["recommend", "--k", "3"]) == 0
    captured = capsys.readouterr()
    assert "could not record this run" in captured.err
    assert "Identity coverage" in captured.out


# --- The CLI verbs ------------------------------------------------------


def test_runs_list_reports_an_empty_store_as_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    assert main(["runs", "list"]) == 0
    assert "no run manifests recorded yet" in capsys.readouterr().out


def test_runs_list_names_an_unreadable_manifest_instead_of_hiding_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # One corrupt file must not hide every readable one, and skipping it in
    # silence would hide it entirely.
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    write_manifest(_manifest("20260101T000000", []), data_dir=tmp_path)
    (runs_dir(tmp_path) / "20260102T000000-recommend.json").write_text("{", encoding="utf-8")
    assert main(["runs", "list"]) == 0
    out = capsys.readouterr().out
    assert "20260101T000000" in out
    assert "UNREADABLE" in out


def test_runs_show_prints_the_manifest_and_an_unknown_id_exits_non_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    write_manifest(_manifest("20260101T000000", [_entry("a", 1, 1, 1)]), data_dir=tmp_path)
    assert main(["runs", "show", "20260101"]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "20260101T000000"
    assert main(["runs", "show", "nope"]) == 2


def test_diff_emits_json_on_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    write_manifest(_manifest("20260101T000000", [_entry("a", 1, 1, 1)]), data_dir=tmp_path)
    write_manifest(
        _manifest("20260102T000000", [_entry("a", 2, 1, 2)]),
        data_dir=tmp_path,
    )
    assert main(["diff", "20260101", "20260102", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["shifts"][0]["cause"] == CAUSE_LENS
    assert payload["shifts"][0]["delta"] == 1
    assert payload["unchanged"] is False


def test_diff_across_different_questions_exits_non_zero_and_says_which(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    write_manifest(_manifest("20260101T000000", [_entry("a", 1, 1, 1)]), data_dir=tmp_path)
    write_manifest(
        _manifest("20260102T000000", [_entry("a", 1, 1, 1)], lens_name="queer"),
        data_dir=tmp_path,
    )
    assert main(["diff", "20260101", "20260102"]) == 2
    assert "lens_name" in capsys.readouterr().err
    assert main(["diff", "20260101", "20260102", "--allow-mixed"]) == 0


def test_runs_prune_keeps_what_it_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    for index in range(4):
        write_manifest(_manifest(f"2026010{index}T000000", []), data_dir=tmp_path)
    assert main(["runs", "prune", "--keep", "1"]) == 0
    assert "pruned 3" in capsys.readouterr().out
    assert len(list_manifest_paths(tmp_path)) == 1


def test_report_and_export_record_their_own_surface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    assert main(["export", "--k", "3", "--out", str(tmp_path / "out.txt")]) == 0
    assert main(["report", "--k", "3", "--out", str(tmp_path / "out.html")]) == 0
    surfaces = [read_manifest(p).surface for p in list_manifest_paths(tmp_path)]
    assert surfaces == ["export", "report"]


def test_report_records_the_explore_value_it_actually_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `report` exposes no `--explore`, so the manifest must record the 0.0 the
    # run used rather than leaving a field a reader would fill in from the
    # command line they think they typed.
    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    assert main(["report", "--k", "3", "--out", str(tmp_path / "out.html")]) == 0
    assert read_manifest(list_manifest_paths(tmp_path)[-1]).explore == 0.0


def test_a_manifest_survives_a_round_trip_through_json(tmp_path: Path) -> None:
    original = _manifest("20260101T000000", [_entry("a", 1, 1, 1), _entry("b", 2, 3, 2)])
    path = write_manifest(original, data_dir=tmp_path)
    assert read_manifest(path) == original
    # And a field added without a reader is caught: `from_dict` builds every
    # field explicitly, so an entry gaining a key it does not read fails here.
    assert replace(original, k=99) != original
