"""Upstream edit deep links (EXP-05): pure, egress-free, never a guess."""

from __future__ import annotations

from recommender.upstream import upstream_edit_url


def test_wikidata_p21_anchors_the_entity_page_at_p21() -> None:
    url = upstream_edit_url("wikidata-p21", "https://www.wikidata.org/wiki/Q16735549")
    assert url == "https://www.wikidata.org/wiki/Q16735549#P21"


def test_wikidata_p21_extracts_qid_from_anywhere_in_the_citation() -> None:
    url = upstream_edit_url("wikidata-p21", "https://www.wikidata.org/entity/Q28907802")
    assert url == "https://www.wikidata.org/wiki/Q28907802#P21"


def test_musicbrainz_gender_links_the_artist_edit_page() -> None:
    mbid = "b7ffd2af-418f-4be2-bdd1-22f8b48613da"
    url = upstream_edit_url("musicbrainz-gender", f"https://musicbrainz.org/artist/{mbid}")
    assert url == f"https://musicbrainz.org/artist/{mbid}/edit"


def test_musicbrainz_relationship_also_links_the_artist_edit_page() -> None:
    mbid = "b7ffd2af-418f-4be2-bdd1-22f8b48613da"
    url = upstream_edit_url("musicbrainz-relationship", f"https://musicbrainz.org/artist/{mbid}")
    assert url == f"https://musicbrainz.org/artist/{mbid}/edit"


def test_none_for_unknown_source_kind() -> None:
    assert upstream_edit_url("artist-statement", "https://example.org/interview") is None


def test_none_for_band_composition_only_kind_with_no_edit_surface() -> None:
    assert upstream_edit_url("discogs-lineup", "https://www.discogs.com/artist/big-thief") is None


def test_none_for_unparseable_wikidata_citation() -> None:
    assert upstream_edit_url("wikidata-p21", "not a url") is None


def test_none_for_unparseable_musicbrainz_citation() -> None:
    assert upstream_edit_url("musicbrainz-gender", "not a url") is None


def test_none_for_empty_citation() -> None:
    assert upstream_edit_url("wikidata-p21", "") is None
    assert upstream_edit_url("musicbrainz-gender", "") is None


def test_never_fabricates_a_write_or_api_url() -> None:
    """The link must be the human-facing edit UI, never an API-write endpoint."""
    url = upstream_edit_url("wikidata-p21", "https://www.wikidata.org/wiki/Q1")
    assert url is not None
    assert "api.php" not in url
    assert "ws/2" not in url  # MusicBrainz's web-service (API) path prefix


# --- #92: the axis with no edit link at all ----------------------------------


def test_wikidata_p91_gets_an_edit_link_anchored_at_the_orientation_statement() -> None:
    """The kind most likely to be wrong was the one kind with no way to fix it.

    ADR 0011 admits P91 for coverage while saying it is "more often a
    biographer's characterization than someone's own words". `upstream_edit_url`
    handled `wikidata-p21` and the two MusicBrainz kinds and fell through to
    `None` for P91, so EXP-05's fix-at-source affordance was offered for every
    claim except that one.
    """
    url = upstream_edit_url("wikidata-p91", "https://www.wikidata.org/wiki/Q12345")
    assert url == "https://www.wikidata.org/wiki/Q12345#P91"


def test_the_p91_link_is_the_human_edit_ui_not_an_api_endpoint() -> None:
    url = upstream_edit_url("wikidata-p91", "https://www.wikidata.org/wiki/Q1")
    assert url is not None
    assert "api.php" not in url
    assert "Special:" not in url


def test_none_for_unparseable_p91_citation() -> None:
    assert upstream_edit_url("wikidata-p91", "not a url") is None
    assert upstream_edit_url("wikidata-p91", "") is None


def test_every_wikidata_source_kind_has_an_anchor() -> None:
    """Derived, so the next Wikidata property cannot ship without an edit link.

    P91 was missing for as long as the mapping was a hand-written ``if`` per
    property. This asserts the mapping covers the enum rather than trusting that
    somebody remembered — the same failure this whole fix is an instance of.
    """
    from pipeline.models import SourceKind
    from recommender.upstream import _WIKIDATA_ANCHORS

    wikidata_kinds = {k.value for k in SourceKind if k.value.startswith("wikidata-")}
    assert wikidata_kinds == set(_WIKIDATA_ANCHORS), (
        "a wikidata source kind exists with no upstream edit anchor"
    )
    for kind in sorted(wikidata_kinds):
        url = upstream_edit_url(kind, "https://www.wikidata.org/wiki/Q1")
        assert url is not None and url.startswith("https://www.wikidata.org/wiki/Q1#")
