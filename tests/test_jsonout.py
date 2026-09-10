"""The CLI's ``--json`` documents, and the guarantees their shape is supposed to carry.

Three things are worth testing here and one thing is not. Not worth testing: that
a document has keys. Worth testing:

* **The no-inference guarantee is structural.** A pick whose identity basis is
  ``unknown`` must carry no provenance and a ``sourced_gender`` of ``"unknown"``,
  and a pick with a sourced gender must carry provenance. That is the property a
  reviewer runs ``recommend --json | jq`` to check, so it is checked here over
  generated inputs rather than one fixture.
* **The schemas describe the documents.** Committed bytes are regenerated and
  compared, real command output is validated against them, and the validator's
  own keyword coverage is asserted -- otherwise a checker could pass a document
  by quietly ignoring the one constraint that mattered.
* **`recommend --json` is byte-stable.** Across *separate interpreters* with
  different ``PYTHONHASHSEED`` values, and over a world with enough picks that
  order is observable. Two renders inside one interpreter would prove nothing:
  set iteration over strings is stable there, so a dropped sort is invisible.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pipeline import cli, jsonout
from pipeline.cli import main
from pipeline.doctor import NETWORK_EGRESS_MODULES

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "schemas"


def run_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, Any]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


def schema(name: str) -> dict[str, Any]:
    return dict(json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8")))


def assert_valid(document: Any, name: str) -> None:
    errors = jsonout.validate(document, schema(name))
    assert errors == [], errors


# --- the schemas describe the documents --------------------------------------


def test_committed_schemas_match_the_definitions() -> None:
    for name in sorted(jsonout.SCHEMAS):
        committed = (SCHEMA_DIR / name).read_text(encoding="utf-8")
        assert committed == jsonout.render_schema(name), (
            f"{name} is stale; run `python3 scripts/gen_schemas.py` and commit the result"
        )


def test_the_validator_understands_every_keyword_the_schemas_use() -> None:
    """A checker that ignores a keyword passes documents it should refuse."""
    for name in sorted(jsonout.SCHEMAS):
        used = jsonout.schema_keywords(jsonout.SCHEMAS[name]())
        unsupported = sorted(used - jsonout.SUPPORTED_KEYWORDS)
        assert unsupported == [], f"{name} uses {unsupported}"


def test_every_document_has_its_own_schema_version() -> None:
    """One shared number would tell every consumer their contract had changed
    when only one document moved."""
    assert set(jsonout.SCHEMA_VERSIONS) == {
        "recommend",
        "report",
        "export",
        "doctor",
        "error",
        "corrections",
        "pending_corrections",
        "diff",
        "feedback",
        "refresh",
        "eval",
        "runs",
    }


def test_every_published_schema_pins_a_version_of_its_own() -> None:
    """The list above is written out on purpose, so a new document has to be
    added to it deliberately. This is the other half: a schema that shipped
    without a version key of its own, or that pinned somebody else's, would
    pass that assertion and still publish a contract nobody can version."""
    for name in sorted(jsonout.SCHEMAS):
        document = jsonout.SCHEMAS[name]()
        pinned = document["properties"]["schema_version"]["const"]
        key = name.removesuffix(".schema.json").replace("-", "_")
        assert key in jsonout.SCHEMA_VERSIONS, f"{name} has no version key"
        assert pinned == jsonout.SCHEMA_VERSIONS[key], name


def test_recommend_output_validates(capsys: pytest.CaptureFixture[str]) -> None:
    code, document = run_json(capsys, ["recommend", "--json", "--k", "5"])
    assert code == 0
    assert_valid(document, "recommend")


def test_export_output_validates(capsys: pytest.CaptureFixture[str]) -> None:
    code, document = run_json(capsys, ["export", "--json", "--k", "3", "--format", "csv"])
    assert code == 0
    assert_valid(document, "export")


def test_doctor_output_validates(capsys: pytest.CaptureFixture[str]) -> None:
    code, document = run_json(capsys, ["doctor", "--json"])
    assert code in (0, 1)
    assert_valid(document, "doctor")


def test_a_refusal_validates_and_exits_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    code, document = run_json(
        capsys, ["recommend", "--json", "--year-from", "2010", "--year-to", "2000"]
    )
    assert code == 2
    assert document["ok"] is False
    assert document["error"]["kind"] == "invalid_filter"
    assert_valid(document, "error")


def test_a_live_mode_refusal_is_json_too(capsys: pytest.CaptureFixture[str]) -> None:
    code, document = run_json(capsys, ["recommend", "--json", "--user", "nobody-cached-here"])
    assert code == 2
    assert document["error"]["kind"] == "live_mode"
    assert_valid(document, "error")


def test_the_validator_rejects_what_the_schema_forbids() -> None:
    """The suite must be able to tell a valid document from an invalid one."""
    document = jsonout.error_document(command="recommend", kind="live_mode", message="x")
    assert jsonout.validate(document, schema("error")) == []
    broken = {**document, "extra": 1}
    assert jsonout.validate(broken, schema("error")) != []
    wrong_version = {**document, "schema_version": 99}
    assert jsonout.validate(wrong_version, schema("error")) != []
    empty_message = {**document, "error": {"kind": "live_mode", "message": ""}}
    assert jsonout.validate(empty_message, schema("error")) != []


def test_an_unknown_error_kind_is_refused_at_construction() -> None:
    with pytest.raises(ValueError):
        jsonout.error_document(command="recommend", kind="vibes", message="x")


def test_render_schema_refuses_an_unknown_name() -> None:
    with pytest.raises(KeyError):
        jsonout.render_schema("nope.schema.json")


def test_a_schema_using_an_unknown_keyword_raises_rather_than_passing() -> None:
    with pytest.raises(ValueError):
        jsonout.validate({}, {"type": "object", "multipleOf": 3})


def test_a_schema_declaring_an_unknown_type_raises() -> None:
    with pytest.raises(ValueError):
        jsonout.validate("x", {"type": "duration"})


# --- the guarantee the document exists to make checkable ----------------------


def _identity_violations(document: dict[str, Any]) -> list[str]:
    """Every way this run's picks break the sourced-or-unknown rule."""
    problems: list[str] = []
    for pick in document["recommendations"]:
        identity = pick["identity"]
        sources = identity["provenance"] + identity["queer_provenance"]
        if identity["inferred"] is not False:
            problems.append(f"{pick['artist_id']}: inferred is not false")
        if identity["basis"] == "unknown":
            if identity["sourced_gender"] != "unknown":
                problems.append(f"{pick['artist_id']}: unknown basis with a sourced gender")
            if sources:
                problems.append(f"{pick['artist_id']}: unknown basis carrying provenance")
        if identity["sourced_gender"] != "unknown" and not identity["provenance"]:
            problems.append(f"{pick['artist_id']}: a gender label with no citation")
    return problems


@settings(deadline=None, max_examples=25)
@given(
    k=st.integers(min_value=1, max_value=12),
    lens=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    explore=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    lens_name=st.sampled_from(["women-nonbinary", "queer"]),
    hide_men=st.booleans(),
)
def test_no_pick_ever_carries_an_unsourced_identity_label(
    k: int, lens: float, explore: float, lens_name: str, hide_men: bool
) -> None:
    """The property `recommend --json | jq` is supposed to let a reviewer check.

    Run over the library rather than the CLI so hypothesis can drive it: the
    document builder is the thing under test, and the CLI is glue over it.
    """
    from pipeline.cache import DEFAULT_DB_PATH, Cache
    from pipeline.demo import demo_catalog, demo_profile, demo_source
    from recommender.coverage import identity_coverage
    from recommender.hybrid import recommend
    from recommender.lens import LENSES

    profile, catalog, source = demo_profile(), demo_catalog(), demo_source()
    with Cache(DEFAULT_DB_PATH) as cache:
        feedbacks = cache.load_feedback(profile.username)
    recs = recommend(
        profile,
        catalog,
        source,
        k=k,
        lens_strength=lens,
        explore=explore,
        feedbacks=feedbacks,
        hide_sourced_men=hide_men,
        lens=LENSES[lens_name],
    )
    document = jsonout.recommend_document(
        recommendations=recs,
        coverage=identity_coverage(recs),
        listener=profile.username,
        lens_name=lens_name,
        lens_strength=lens,
        explore=explore,
        hide_sourced_men=hide_men,
        k=k,
        content_filter_description="filters: none",
    )
    assert _identity_violations(document) == []
    assert jsonout.validate(document, schema("recommend")) == []


def test_the_violation_detector_can_actually_see_a_violation() -> None:
    """The property above is only worth something if its checker fires.

    A guard that cannot detect the defect it guards against reads as a pass on
    every run, so the checker gets a negative control of its own.
    """
    document: dict[str, Any] = {
        "recommendations": [
            {
                "artist_id": "made-up",
                "identity": {
                    "basis": "unknown",
                    "sourced_gender": "woman",
                    "inferred": False,
                    "provenance": [],
                    "queer_provenance": [],
                },
            }
        ]
    }
    assert _identity_violations(document) != []


def test_an_empty_run_reports_no_share_rather_than_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A zero share would read as a measurement. There is none over no picks."""
    code, document = run_json(
        capsys,
        ["recommend", "--json", "--include-tags", "no-artist-carries-this-tag"],
    )
    assert code == 0
    assert document["recommendations"] == []
    assert document["identity_coverage"]["total"] == 0
    assert document["identity_coverage"]["sourced_share"] is None
    assert document["identity_coverage"]["unknown_share"] is None
    assert_valid(document, "recommend")


# --- doctor publishes what the tool may contact -------------------------------


def test_doctor_json_publishes_the_egress_allowlist(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _code, document = run_json(capsys, ["doctor", "--json"])
    assert set(document["egress_allowlist"]["modules"]) == set(NETWORK_EGRESS_MODULES)
    assert document["egress_allowlist"]["hosts"]
    assert all(host.startswith("https://") for host in document["egress_allowlist"]["hosts"])


def test_doctor_json_says_whether_upstream_was_probed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without --check-upstream there are no upstream checks at all, and their
    absence is not a report that every API was reachable."""
    _code, document = run_json(capsys, ["doctor", "--json"])
    assert document["upstream_checked"] is False
    assert not [c for c in document["checks"] if c["name"].startswith("upstream:")]


# --- byte stability -----------------------------------------------------------

_STABILITY_SCRIPT = (
    "import sys; from pipeline.cli import main; "
    "sys.exit(main(['recommend', '--json', '--k', '12', '--explore', '0.7']))"
)


def _render_under(seed: str) -> str:
    environment = {**os.environ, "PYTHONHASHSEED": seed}
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _STABILITY_SCRIPT],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    )
    return completed.stdout


def test_recommend_json_is_byte_identical_across_interpreters() -> None:
    """Across processes, with the hash seed varied, over a world of twelve picks.

    Every clause is load-bearing. Two renders inside one interpreter share a
    stable set iteration order, so a dropped `sorted()` is invisible to them.
    And a fixture with one row has no order to get wrong: `--k 12` and a
    non-zero `--explore` are what make ordering observable at all.
    """
    renders = [_render_under(seed) for seed in ("0", "1", "12345")]
    assert renders[0] == renders[1] == renders[2]
    # And it is a real document, not three identical error pages.
    document = json.loads(renders[0])
    assert document["command"] == "recommend"
    # The demo world yields seven picks here. Asserted so that a future change
    # shrinking it cannot quietly make this a one-row fixture, where there is no
    # order left to get wrong and the guard would pass on anything.
    assert len(document["recommendations"]) >= 5
    assert_valid(document, "recommend")


# --- the two ledger documents -------------------------------------------------
#
# These are the surfaces a reviewer reads to check the claim one layer earlier
# than `recommend --json`: this is what a person asserted and cited; that is what
# the ranking then did with it. The assertions below are about the two ways that
# reading could go wrong -- a value leaking into a document that is likely to be
# piped into a log, and an emptiness that means "not done" rendering as one that
# means "measured and nothing there".


def _ledger(tmp_path: Path) -> str:
    return str(tmp_path / "ledger.db")


def test_the_corrections_ledger_lists_with_its_citations(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    database = _ledger(tmp_path)
    assert (
        main(
            [
                "corrections",
                "--db",
                database,
                "--artist",
                "ar-1",
                "--value",
                "woman",
                "--citation",
                "https://example.invalid/interview",
                "--retrieved-at",
                "2026-01-02",
            ]
        )
        == 0
    )
    capsys.readouterr()
    code, document = run_json(capsys, ["corrections", "--db", database, "--json"])

    assert code == 0
    assert_valid(document, "corrections")
    assert document["action"] == "list"
    assert document["count"] == 1
    row = document["corrections"][0]
    assert row["artist_id"] == "ar-1"
    assert row["asserted_value"] == "woman"
    assert row["citation"] == "https://example.invalid/interview"
    assert row["retrieved_at"] == "2026-01-02"


def test_an_empty_ledger_reports_a_measured_zero_and_a_write_reports_none(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The one place an empty list is the honest answer, and the one place it is not.

    Reading an empty ledger measured nothing there: `count` is 0 and
    `corrections` is `[]`. A write did not read the ledger at all, so both are
    `null` -- a 0 there would tell a script the ledger is empty in the same
    breath as telling it a row was added.
    """
    database = _ledger(tmp_path)
    _, empty = run_json(capsys, ["corrections", "--db", database, "--json"])
    assert empty["count"] == 0
    assert empty["corrections"] == []
    assert empty["recorded"] is None

    _, written = run_json(
        capsys,
        [
            "corrections",
            "--db",
            database,
            "--json",
            "--artist",
            "ar-2",
            "--value",
            "nonbinary",
            "--citation",
            "https://example.invalid/statement",
        ],
    )
    assert_valid(written, "corrections")
    assert written["action"] == "record"
    assert written["count"] is None
    assert written["corrections"] is None
    assert written["recorded"]["artist_id"] == "ar-2"


def test_a_write_does_not_echo_the_asserted_value_back(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """An identity value is the one thing this project promises never leaves the
    machine it was typed on, and JSON is the output most likely to reach a log.
    The console path already withholds it; the document must not reintroduce it.
    """
    code, document = run_json(
        capsys,
        [
            "corrections",
            "--db",
            _ledger(tmp_path),
            "--json",
            "--artist",
            "ar-3",
            "--value",
            "woman",
            "--citation",
            "https://example.invalid/interview",
        ],
    )

    assert code == 0
    assert "woman" not in json.dumps(document)


def test_a_refused_correction_is_json_when_json_was_asked_for(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A refusal in result shape, and still no echo of the rejected value."""
    code, document = run_json(
        capsys,
        [
            "corrections",
            "--db",
            _ledger(tmp_path),
            "--json",
            "--artist",
            "ar-4",
            "--value",
            "femalee",
            "--citation",
            "https://example.invalid/x",
        ],
    )

    assert code == 1, "the published exit code for this refusal is 1, not 2"
    assert_valid(document, "error")
    assert document["ok"] is False
    assert document["error"]["kind"] == "invalid_input"
    assert "femalee" not in json.dumps(document)


def test_pending_corrections_file_and_list_round_trip(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = str(tmp_path / "pending.json")
    code, filed = run_json(
        capsys,
        [
            "pending-corrections",
            "--path",
            path,
            "--json",
            "add",
            "--artist",
            "ar-5",
            "--source-kind",
            "wikidata",
            "--citation",
            "https://www.wikidata.org/wiki/Q1",
            "--proposed",
            "woman",
        ],
    )

    assert code == 0
    assert_valid(filed, "pending-corrections")
    assert filed["action"] == "file"
    assert filed["count"] is None
    assert filed["pending_corrections"] is None
    assert filed["filed"]["artist_id"] == "ar-5"

    code, listed = run_json(capsys, ["pending-corrections", "--path", path, "--json"])
    assert code == 0
    assert_valid(listed, "pending-corrections")
    assert listed["action"] == "list"
    assert listed["count"] == 1
    assert listed["filed"] is None
    assert listed["pending_corrections"][0]["artist_id"] == "ar-5"


def test_a_row_nothing_has_superseded_reports_null_not_a_blank(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`""` would read as an observation that recorded nothing. There is no
    observation: no refresh has seen this source say anything else yet."""
    path = str(tmp_path / "pending.json")
    run_json(
        capsys,
        [
            "pending-corrections",
            "--path",
            path,
            "--json",
            "add",
            "--artist",
            "ar-6",
            "--source-kind",
            "musicbrainz",
            "--citation",
            "https://musicbrainz.org/artist/x",
            "--proposed",
            "nonbinary",
        ],
    )
    _, listed = run_json(capsys, ["pending-corrections", "--path", path, "--json"])
    row = listed["pending_corrections"][0]
    assert row["is_superseded"] is False
    assert row["superseded_by_value"] is None
    assert row["superseded_at"] is None


def test_an_unknown_source_kind_offers_no_edit_route_rather_than_a_blank_one(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`edit_url` is null when this project knows no edit route for a source
    kind. An empty string would be a link a caller could try to open."""
    path = str(tmp_path / "pending.json")
    run_json(
        capsys,
        [
            "pending-corrections",
            "--path",
            path,
            "--json",
            "add",
            "--artist",
            "ar-7",
            "--source-kind",
            "artist-statement",
            "--citation",
            "https://example.invalid/zine-interview",
            "--proposed",
            "woman",
        ],
    )
    _, listed = run_json(capsys, ["pending-corrections", "--path", path, "--json"])
    assert listed["pending_corrections"][0]["edit_url"] is None


def test_the_text_listings_are_unchanged_when_json_is_not_asked_for(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    database = _ledger(tmp_path)
    assert main(["corrections", "--db", database]) == 0
    assert capsys.readouterr().out.strip() == "no corrections recorded"
    assert main(["pending-corrections", "--path", str(tmp_path / "p.json")]) == 0
    assert capsys.readouterr().out.strip() == "no pending corrections"


# --- diff: the one --json surface that used to publish an unversioned document --


def _diff_manifest(run_id: str, entries: list[Any], **overrides: Any) -> Any:
    from pipeline.runs import RunManifest

    payload: dict[str, Any] = {
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
        "exposure": {"woman": 1.0, "unknown": None},
        "entries": entries,
    }
    payload.update(overrides)
    return RunManifest(**payload)


def _diff_entry(artist_id: str, rank: int, base: int, lens: int) -> Any:
    from pipeline.runs import RunEntry

    return RunEntry(
        artist_id=artist_id,
        name=artist_id.title(),
        rank=rank,
        base_rank=base,
        lens_rank=lens,
        segment="woman",
        basis="self-identified",
    )


@pytest.fixture
def two_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Two comparable runs on disk, with enough movement to fill every list."""
    from pipeline.runs import write_manifest

    monkeypatch.setenv("LAVENDER_DATA_DIR", str(tmp_path))
    write_manifest(
        _diff_manifest(
            "20260101T000000",
            [_diff_entry("a", 1, 1, 1), _diff_entry("b", 2, 2, 2), _diff_entry("c", 3, 3, 3)],
        ),
        data_dir=tmp_path,
    )
    write_manifest(
        _diff_manifest(
            "20260102T000000",
            [_diff_entry("a", 1, 1, 1), _diff_entry("c", 2, 3, 2), _diff_entry("d", 3, 3, 3)],
        ),
        data_dir=tmp_path,
    )
    return tmp_path


def test_diff_output_validates_against_its_committed_schema(
    capsys: pytest.CaptureFixture[str], two_runs: Path
) -> None:
    code, document = run_json(capsys, ["diff", "20260101", "20260102", "--json"])
    assert code == 0
    assert_valid(document, "diff")
    # A presence assertion beside the validation: a document with empty lists
    # would validate too, and would prove nothing about the schema describing
    # the shapes that carry data.
    assert document["entered"] and document["left"] and document["shifts"]


def test_a_refused_diff_is_a_document_on_stdout_not_a_sentence_on_stderr(
    capsys: pytest.CaptureFixture[str], two_runs: Path
) -> None:
    """The behaviour every other ``--json`` surface promises in its own help text.

    Before this, ``diff --json`` printed the refusal to stderr and left stdout
    empty, so ``lavender diff --json | jq`` received nothing at all -- which a
    caller reads as "no differences" rather than as "this did not run".
    """
    from pipeline.runs import write_manifest

    write_manifest(
        _diff_manifest("20260103T000000", [_diff_entry("a", 1, 1, 1)], lens_name="queer"),
        data_dir=two_runs,
    )
    code, document = run_json(capsys, ["diff", "20260101", "20260103", "--json"])
    assert code != 0
    assert_valid(document, "error")
    assert document["ok"] is False
    assert document["command"] == "diff"
    assert document["error"]["kind"] == "invalid_input"
    assert "lens_name" in document["error"]["message"]


def test_an_unresolvable_run_id_is_not_found_rather_than_invalid_input(
    capsys: pytest.CaptureFixture[str], two_runs: Path
) -> None:
    """Two refusals, two next moves, two kinds.

    ``not_found`` sends the caller to `lavender runs list`; ``invalid_input``
    sends them to ``--allow-mixed``. One kind for both would leave a script
    unable to tell the fixable case from the one that is not.
    """
    code, document = run_json(capsys, ["diff", "nosuchrun", "20260102", "--json"])
    assert code != 0
    assert_valid(document, "error")
    assert document["error"]["kind"] == "not_found"


def test_every_delta_value_is_a_number_or_null_never_a_string(
    capsys: pytest.CaptureFixture[str], two_runs: Path
) -> None:
    """The constraint ``diff.schema.json`` is structurally unable to carry.

    ``coverage_delta`` and ``exposure_delta`` are keyed by whatever the two
    manifests recorded, so the schema can only say "object"; this is the rest
    of the contract. ``null`` is load-bearing -- it means one side did not
    record the figure, which is not a zero change.
    """
    _, document = run_json(capsys, ["diff", "20260101", "20260102", "--json"])
    seen_null = False
    for field in ("coverage_delta", "exposure_delta"):
        assert document[field], f"{field} is empty; this fixture proves nothing"
        for key, value in document[field].items():
            assert value is None or (
                isinstance(value, (int, float)) and not isinstance(value, bool)
            ), f"{field}[{key}] is {value!r}"
            seen_null = seen_null or value is None
    assert seen_null, "no null reached the document; the null rule is untested here"


def test_the_diff_text_rendering_is_unchanged_when_json_is_not_asked_for(
    capsys: pytest.CaptureFixture[str], two_runs: Path
) -> None:
    assert main(["diff", "20260101", "20260102"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("20260101T000000 -> 20260102T000000")
    assert "{" not in out


# ---------------------------------------------------------------------------
# The five surfaces #121 was still open for: report, feedback, refresh, eval,
# runs. Each one is a document with a committed schema, and each carries one
# distinction the text rendering had been keeping in prose.
# ---------------------------------------------------------------------------


def test_report_output_validates_and_still_writes_the_page(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`--json` describes the run; it does not replace the artifact."""
    out = tmp_path / "discoveries.html"
    code, document = run_json(capsys, ["report", "--json", "--k", "5", "--out", str(out)])
    assert code == 0
    assert_valid(document, "report")
    assert out.is_file(), "report --json stopped writing the page it exists to write"
    assert document["written_to"] == str(out)
    assert document["bytes_written"] == len(out.read_bytes())


def test_report_and_recommend_describe_the_same_ranking(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """One serialisation of a pick list, not two that can drift.

    The dashboard once showed one ranking while measuring another; both verbs
    build their picks through the same function so that cannot recur here.
    """
    _, report = run_json(
        capsys, ["report", "--json", "--k", "5", "--out", str(tmp_path / "r.html")]
    )
    _, recommend = run_json(capsys, ["recommend", "--json", "--k", "5", "--lens", "0.5"])
    assert [p["artist_id"] for p in report["recommendations"]] == [
        p["artist_id"] for p in recommend["recommendations"]
    ]


def test_feedback_output_validates(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code, document = run_json(
        capsys,
        ["feedback", "--artist", "a1", "--up", "--db", str(tmp_path / "cache.db"), "--json"],
    )
    assert code == 0
    assert_valid(document, "feedback")
    assert document["vote"] == "up"


def test_runs_list_output_validates(capsys: pytest.CaptureFixture[str]) -> None:
    code, document = run_json(capsys, ["runs", "list", "--json"])
    assert code == 0
    assert_valid(document, "runs")
    assert document["action"] == "list"


def test_a_manifest_that_cannot_be_read_is_not_counted_as_a_run(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two lists, because they are two facts.

    Folding an unreadable file into the rows publishes it as a run that
    happened; dropping it reports a smaller population as a complete one. The
    text rendering already kept them apart and the document has to as well.
    """
    broken = tmp_path / "deadbeef.json"
    broken.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(cli, "list_manifest_paths", lambda: [broken])
    code, document = run_json(capsys, ["runs", "list", "--json"])
    assert code == 0
    assert_valid(document, "runs")
    assert document["runs"] == []
    assert [row["name"] for row in document["unreadable"]] == ["deadbeef"]


def test_refresh_demo_reports_null_upstream_counts_rather_than_zeroes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The demo branch queries nothing, so it counts nothing.

    `attempted: 0` beside `answered: false` is what a live run that reached
    nothing looks like. A demo run must not be able to produce it -- that is
    the one distinction `RefreshOutcome` was written to keep.
    """
    code, document = run_json(capsys, ["refresh", "--db", str(tmp_path / "cache.db"), "--json"])
    assert code == 0
    assert_valid(document, "refresh")
    assert document["mode"] == "demo"
    assert document["upstream"]["queried"] is False
    for field in ("answered", "attempted", "verified", "unverified", "failed", "protected"):
        assert document["upstream"][field] is None, (
            f"the demo branch published a count for {field}, which no upstream supplied"
        )


def test_eval_output_validates_and_reports_no_baseline_as_null(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`false` would say nothing regressed. Nothing was compared."""
    code, document = run_json(
        capsys,
        [
            "eval",
            "--json",
            "--out",
            str(tmp_path / "eval.json"),
            "--baseline",
            str(tmp_path / "absent.json"),
        ],
    )
    assert code in (0, 1)
    assert_valid(document, "eval")
    assert document["regressed_vs_baseline"] is None
    assert document["baseline_present"] is False


def test_eval_names_the_guarantees_that_passed_over_an_empty_segment(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """A guarantee with nothing to violate is not a guarantee that held.

    These are `UNMEASURED:` sentences on stderr today, invisible to anything
    reading the exit code.
    """
    _, document = run_json(
        capsys,
        [
            "eval",
            "--json",
            "--out",
            str(tmp_path / "eval.json"),
            "--baseline",
            str(tmp_path / "absent.json"),
        ],
    )
    for item in document["unmeasured_guarantees"]:
        assert item["guarantee"]
        assert item["note"]
        assert isinstance(item["base_count"], int)


def test_the_text_renderings_of_the_five_new_surfaces_are_unchanged(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Without `--json`, every one of them prints what it always printed."""
    for argv in (
        ["report", "--k", "3", "--out", str(tmp_path / "a.html")],
        ["feedback", "--artist", "a1", "--up", "--db", str(tmp_path / "c.db")],
        ["runs", "list"],
        ["refresh", "--db", str(tmp_path / "c.db")],
    ):
        capsys.readouterr()
        assert cli.main(argv) == 0
        out = capsys.readouterr().out
        assert out, f"{argv[0]} printed nothing without --json"
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)
