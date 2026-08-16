"""Local pending-corrections store (EXP-05): the local half of the round-trip.

local note -> upstream edit (human, out of band) -> a refresh against a real
upstream source observes the *proposed value* -> `reconcile()` clears the row.

The middle step is load-bearing and used not to be checked (#70). `reconcile()`
matched on `(artist_id, source_kind)` alone, so a refresh that moved only a
retrieval date deleted a person's filed note while reporting success. The test
below that "verified" this — `test_reconcile_drops_matching_row_and_keeps_others`
— passed a change whose `old_value` and `new_value` were byte-identical, against
a row whose `current_value` and `proposed_value` were also identical, and
asserted the row was dropped. It pinned the defect as correct behaviour. It is
rewritten here to assert the absence of the harm instead: a filed row is never
removed without evidence that upstream now asserts what was proposed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pipeline import corrections
from pipeline.ingest import IdentityLabelChange

_TODAY = "2026-08-14"


def _file(path: Path, **kwargs: str) -> corrections.PendingCorrection:
    defaults = {
        "artist_id": "mitski",
        "source_kind": "wikidata-p21",
        "citation": "https://www.wikidata.org/wiki/Q16735549",
        "current_value": "Q6581072",
        "proposed_value": "Q48270",
        "note": "P21 is wrong; the artist has stated otherwise publicly.",
        "filed_at": "2026-07-01",
    }
    defaults.update(kwargs)
    return corrections.add_correction(path, **defaults)  # type: ignore[arg-type]


def _change(**kwargs: str) -> IdentityLabelChange:
    defaults = {
        "artist_id": "mitski",
        "source_kind": "wikidata-p21",
        "old_value": "Q6581072",
        "new_value": "Q6581072",
        "retrieved_at": "2026-08-14",
    }
    defaults.update(kwargs)
    return IdentityLabelChange(**defaults)  # type: ignore[arg-type]


def test_add_correction_persists_and_list_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "pending-corrections.json"
    row = corrections.add_correction(
        path,
        artist_id="mitski",
        source_kind="wikidata-p21",
        citation="https://www.wikidata.org/wiki/Q16735549",
        current_value="Q6581072",
        proposed_value="Q6581072",
        note="Wikidata P21 is stale; artist has since clarified pronouns publicly.",
        filed_at="2026-07-01",
        edit_url="https://www.wikidata.org/wiki/Q16735549#P21",
    )
    assert row.artist_id == "mitski"

    rows = corrections.list_corrections(path)
    assert len(rows) == 1
    assert rows[0] == row


def test_list_corrections_on_missing_file_is_empty(tmp_path: Path) -> None:
    assert corrections.list_corrections(tmp_path / "nope.json") == []


def test_add_correction_appends_not_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "pending-corrections.json"
    corrections.add_correction(
        path,
        artist_id="a",
        source_kind="wikidata-p21",
        citation="https://www.wikidata.org/wiki/Q1",
        current_value="x",
        proposed_value="y",
        note="",
        filed_at="2026-07-01",
    )
    corrections.add_correction(
        path,
        artist_id="b",
        source_kind="musicbrainz-gender",
        citation="https://musicbrainz.org/artist/b",
        current_value="x",
        proposed_value="y",
        note="",
        filed_at="2026-07-01",
    )
    rows = corrections.list_corrections(path)
    assert {r.artist_id for r in rows} == {"a", "b"}


def test_reconcile_clears_a_row_when_upstream_asserts_the_proposed_value(
    tmp_path: Path,
) -> None:
    """The round-trip completing is the *only* thing that removes a row."""
    path = tmp_path / "pending-corrections.json"
    _file(path)
    _file(path, artist_id="snail-mail", source_kind="musicbrainz-gender")

    outcome = corrections.reconcile(path, [_change(new_value="Q48270")], observed_at=_TODAY)
    assert [row.artist_id for row in outcome.reconciled] == ["mitski"]
    assert outcome.superseded == ()
    assert [row.artist_id for row in corrections.list_corrections(path)] == ["snail-mail"]


def test_a_date_only_change_never_removes_a_filed_row(tmp_path: Path) -> None:
    """#70's reproduction: the asserted value was byte-identical before and after.

    Only ``retrieved_at`` moved. That is real lineage and ``_diff_sources``
    rightly still reports it, but it is not evidence that anybody edited
    anything, and it must not delete a person's note.
    """
    path = tmp_path / "pending-corrections.json"
    _file(path)
    outcome = corrections.reconcile(path, [_change(retrieved_at="2026-08-14")], observed_at=_TODAY)
    assert outcome.reconciled == ()
    assert outcome.superseded == ()
    assert len(corrections.list_corrections(path)) == 1


def test_a_change_to_some_other_value_supersedes_but_never_deletes(tmp_path: Path) -> None:
    """Upstream moved, but not to what was proposed: keep the row, and say so."""
    path = tmp_path / "pending-corrections.json"
    _file(path)
    outcome = corrections.reconcile(path, [_change(new_value="Q6581097")], observed_at=_TODAY)
    assert outcome.reconciled == ()
    assert len(outcome.superseded) == 1
    rows = corrections.list_corrections(path)
    assert len(rows) == 1, "a superseded row must stay on file"
    assert rows[0].superseded_by_value == "Q6581097"
    assert rows[0].superseded_at == _TODAY
    assert rows[0].proposed_value == "Q48270"  # the person's proposal is preserved
    assert "still open" in rows[0].describe()
    assert any("you proposed" in line for line in outcome.report_lines())


def test_the_vocabulary_decides_whether_two_asserted_values_are_the_same_claim(
    tmp_path: Path,
) -> None:
    """A ``woman`` proposal is satisfied by an upstream ``female``, and vice versa."""
    path = tmp_path / "pending-corrections.json"
    _file(
        path,
        source_kind="musicbrainz-gender",
        citation="https://musicbrainz.org/artist/x",
        current_value="male",
        proposed_value="woman",
    )
    outcome = corrections.reconcile(
        path,
        [_change(source_kind="musicbrainz-gender", old_value="male", new_value="female")],
        observed_at=_TODAY,
    )
    assert len(outcome.reconciled) == 1
    assert corrections.list_corrections(path) == []


def test_reconcile_with_no_matching_changes_leaves_all_rows(tmp_path: Path) -> None:
    path = tmp_path / "pending-corrections.json"
    _file(path)
    outcome = corrections.reconcile(
        path, [_change(artist_id="someone-else", new_value="Q48270")], observed_at=_TODAY
    )
    assert outcome.reconciled == ()
    assert len(corrections.list_corrections(path)) == 1


def test_reconcile_on_empty_pending_list_is_a_noop(tmp_path: Path) -> None:
    path = tmp_path / "pending-corrections.json"
    outcome = corrections.reconcile(path, [], observed_at=_TODAY)
    assert outcome.reconciled == ()
    assert outcome.still_open == ()


def test_reconcile_with_no_changes_at_all_is_a_noop(tmp_path: Path) -> None:
    path = tmp_path / "pending-corrections.json"
    _file(path)
    outcome = corrections.reconcile(path, [], observed_at=_TODAY)
    assert outcome.reconciled == ()
    assert len(corrections.list_corrections(path)) == 1


# --- "upstream" must have an upstream behind it ------------------------------


def test_nothing_reconciles_when_no_upstream_was_queried(tmp_path: Path) -> None:
    """The shipped ``lavender refresh`` queries nothing, so it may reconcile nothing.

    Even handed a change that *would* match, the demo path must not clear a row:
    with no upstream consulted, no upstream edit could have landed.
    """
    path = tmp_path / "pending-corrections.json"
    _file(path)
    outcome = corrections.reconcile_after_refresh(
        path,
        [_change(new_value="Q48270")],
        upstream_queried=False,
        observed_at=_TODAY,
    )
    assert outcome.reconciled == ()
    assert outcome.upstream_queried is False
    assert len(corrections.list_corrections(path)) == 1
    report = "\n".join(outcome.report_lines())
    assert "reconciled 0" in report
    assert "no upstream identity source was queried" in report
    assert "1 pending correction(s) still open" in report


def test_the_live_path_does_reconcile(tmp_path: Path) -> None:
    """The same call with a real upstream behind it closes the round-trip (FIX-01)."""
    path = tmp_path / "pending-corrections.json"
    _file(path)
    outcome = corrections.reconcile_after_refresh(
        path,
        [_change(new_value="Q48270")],
        upstream_queried=True,
        observed_at=_TODAY,
    )
    assert len(outcome.reconciled) == 1
    assert corrections.list_corrections(path) == []


@pytest.mark.parametrize(
    ("proposed", "observed", "expect_reconciled"),
    [
        ("Q48270", "Q48270", True),  # exactly what was proposed
        ("Q48270", "Q6581072", False),  # upstream moved somewhere else
        ("Q48270", "Q1052281", False),  # a different gender entirely
        ("not-in-the-vocabulary", "not-in-the-vocabulary", True),  # literal fallback
        ("not-in-the-vocabulary", "something-else", False),
    ],
)
def test_only_the_proposed_value_reconciles(
    tmp_path: Path, proposed: str, observed: str, expect_reconciled: bool
) -> None:
    path = tmp_path / "pending-corrections.json"
    _file(path, proposed_value=proposed)
    # `old_value` differs from every `observed` below, so each case is decided by
    # the value comparison and never by the date-only filter.
    outcome = corrections.reconcile(
        path,
        [_change(old_value="Q-was-something-else", new_value=observed)],
        observed_at=_TODAY,
    )
    assert bool(outcome.reconciled) is expect_reconciled
    assert bool(corrections.list_corrections(path)) is not expect_reconciled


def test_default_path_sits_alongside_the_given_db() -> None:
    assert corrections.default_path("data/cache.db") == Path("data/pending-corrections.json")
