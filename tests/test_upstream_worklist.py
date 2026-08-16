"""The upstream worklist's classifier (scripts/upstream_worklist.py).

Each category maps to a *different kind of edit with different care rules*, so
mixing them up is the failure that matters here: telling someone a band needs a
gender edit when what it needs is a lineup role would push an identity claim
where none was called for.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "upstream_worklist", Path(__file__).parents[1] / "scripts" / "upstream_worklist.py"
)
assert _SPEC and _SPEC.loader
worklist = importlib.util.module_from_spec(_SPEC)
sys.modules["upstream_worklist"] = worklist
_SPEC.loader.exec_module(worklist)

MBID = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"


class FakeUpstream:
    """Stands in for the cached MusicBrainz payloads."""

    def __init__(self, record: dict[str, Any] | None) -> None:
        self._record = record

    def mbid_for(self, artist_id: str) -> str | None:
        return MBID if self._record is not None else None

    def document(self, url: str) -> dict[str, Any] | None:
        return self._record


def artist(**kw: Any) -> dict[str, Any]:
    return {"artist_id": MBID, "name": "An Act", "identity": {"gender": "unknown"}, **kw}


def group(relations: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "Group", "relations": relations}


def member(*attributes: str) -> dict[str, Any]:
    return {
        "type": "member of band",
        "attributes": list(attributes),
        "artist": {"id": MBID, "name": "A Person"},
    }


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        (group([member("guitar"), member("drums")]), "fronting-role"),
        (group([]), "no-lineup"),
        (group([member("lead vocals")]), None),
        ({"type": "Person"}, "person-gender"),
        ({"type": "Person", "gender": "female"}, None),
    ],
)
def test_each_gap_maps_to_the_edit_that_would_close_it(
    record: dict[str, Any], expected: str | None
) -> None:
    item = worklist.classify(artist(), FakeUpstream(record), plays=10)
    assert (item.category if item else None) == expected


def test_a_backing_vocalist_does_not_count_as_fronting() -> None:
    """The worklist must agree with `is_fronting_role`, including its fix."""
    item = worklist.classify(artist(), FakeUpstream(group([member("background vocals")])), plays=1)
    assert item is not None and item.category == "fronting-role"


def test_a_marked_front_person_with_no_gender_is_its_own_category() -> None:
    """One edit, and the only one here that needs a citation."""
    act = artist(
        composition={
            "members_fronting": [
                {"name": "A Singer", "role": "lead vocals", "identity": {"gender": "unknown"}}
            ]
        }
    )
    item = worklist.classify(act, FakeUpstream(group([member("lead vocals")])), plays=5)

    assert item is not None
    assert item.category == "front-person-gender"
    assert "A Singer" in item.detail


def test_an_already_sourced_front_person_needs_nothing() -> None:
    act = artist(
        composition={
            "members_fronting": [
                {"name": "A Singer", "role": "lead vocals", "identity": {"gender": "woman"}}
            ]
        }
    )
    assert worklist.classify(act, FakeUpstream(group([member("lead vocals")])), plays=5) is None


def test_an_artist_we_cannot_resolve_upstream_is_not_an_edit() -> None:
    """No MusicBrainz record means no edit to propose — a local pin is the fix."""
    assert worklist.classify(artist(), FakeUpstream(None), plays=5) is None


def test_the_report_leads_with_the_no_inference_rule() -> None:
    rendered = worklist.render([worklist.Item("person-gender", "X", MBID, 3)], "someone")
    assert "publicly self-identified" in rendered
    assert "edit" in rendered and MBID in rendered


def test_edit_urls_point_at_the_edit_form() -> None:
    assert worklist.Item("no-lineup", "X", MBID, 0).edit_url.endswith(f"{MBID}/edit")


# --- citations --------------------------------------------------------------


def _source(kind: str, citation: str) -> dict[str, Any]:
    return {"kind": kind, "citation": citation, "retrieved_at": "2026-08-16", "detail": "x"}


def test_citations_are_gathered_from_every_axis_a_claim_can_live_on() -> None:
    """Gender, the ADR 0011 second axis, the lineup, and each front-person's own label."""
    act = {
        "identity": {"sources": [_source("musicbrainz-gender", "https://mb/1")]},
        "queer": {
            "orientation_sources": [_source("wikidata-p91", "https://wd/2")],
            "trans_sources": [_source("wikidata-p21", "https://wd/3")],
        },
        "composition": {
            "sources": [_source("musicbrainz-relationship", "https://mb/4")],
            "members_fronting": [
                {"identity": {"sources": [_source("artist-statement", "https://post/5")]}}
            ],
        },
    }
    kinds = [k for k, _ in worklist.existing_citations(act)]

    assert kinds == [
        "musicbrainz-gender",
        "wikidata-p91",
        "wikidata-p21",
        "musicbrainz-relationship",
        "artist-statement",
    ]


def test_a_repeated_citation_is_listed_once() -> None:
    same = _source("wikidata-p21", "https://wd/1")
    act = {"identity": {"sources": [same, same]}}
    assert len(worklist.existing_citations(act)) == 1


def test_an_artist_with_nothing_sourced_has_no_citations() -> None:
    assert worklist.existing_citations({"identity": {"gender": "unknown"}}) == ()


def test_only_identity_edits_are_marked_as_needing_a_citation() -> None:
    """Adding a vocals credit is discography; recording someone's gender is not."""
    needs = {c: worklist.Item(c, "X", MBID, 1).needs_citation for c in worklist.CATEGORIES}
    assert needs == {
        "fronting-role": False,
        "no-lineup": False,
        "person-gender": True,
        "front-person-gender": True,
    }


def test_an_uncited_identity_row_says_so_rather_than_looking_finished() -> None:
    rendered = worklist.render([worklist.Item("person-gender", "X", MBID, 1)], "someone")
    assert "nothing cited yet" in rendered


def test_a_discography_row_is_not_nagged_for_a_citation() -> None:
    rendered = worklist.render([worklist.Item("no-lineup", "X", MBID, 1)], "someone")
    assert "nothing cited yet" not in rendered


def test_existing_citations_are_rendered_as_links() -> None:
    item = worklist.Item(
        "person-gender", "X", MBID, 1, "", (("wikidata-p21", "https://www.wikidata.org/wiki/Q1"),)
    )
    for rendered in (worklist.render([item], "s"), worklist.render_html([item], "s")):
        assert "wikidata-p21" in rendered
        assert "https://www.wikidata.org/wiki/Q1" in rendered


def test_only_identity_rows_get_a_citation_capture_field() -> None:
    """The field appears where a citation is the gate, and nowhere else."""
    gated = worklist.render_html([worklist.Item("person-gender", "X", MBID, 1)], "s")
    plain = worklist.render_html([worklist.Item("fronting-role", "Y", MBID, 1)], "s")

    assert 'class="src"' in gated
    assert 'class="src"' not in plain
