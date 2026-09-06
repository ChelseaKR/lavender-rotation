"""Census: aggregate identity coverage, measured — and never a per-artist export.

The research roadmap's central claim is that unknown is the common case. It has
always been cited from the literature rather than measured on this project's own
data, and `pipeline/census.py` is what closes that. Two things are load-bearing
and both are tested here rather than trusted:

* the aggregate is **aggregate** — no artist id, name, or citation URL — because
  a per-artist identity export is precisely the redistributable dataset this
  project refuses to create (ideation E2, rejected on those grounds);
* a world with nothing enriched reports that plainly, rather than an empty table
  a reader would mistake for a measurement.
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.cache import Cache
from pipeline.census import (
    LINEAGE_NOT_RECORDED,
    NEVER_FETCHED,
    SCHEMA_VERSION,
    UNSUPPORTED_UNKNOWN_REASONS,
    census,
)
from pipeline.demo import DEMO_USER, demo_catalog, demo_profile
from pipeline.identity import IdentityEvidence
from pipeline.models import Gender, SourceKind

COMMITTED = Path(__file__).resolve().parent.parent / "docs" / "audits" / "census-demo.json"

_AS_OF = "2026-09-06"


def _demo_census(**kwargs):
    catalog, profile = demo_catalog(), demo_profile()
    return census(
        catalog,
        as_of=kwargs.pop("as_of", _AS_OF),
        known_artist_ids=[*profile.play_counts, *catalog],
        **kwargs,
    )


def test_demo_totals_equal_the_demo_world() -> None:
    """Totals are the world, and the gender buckets partition it exactly."""
    catalog, profile = demo_catalog(), demo_profile()
    expected_total = len({*profile.play_counts, *catalog})
    report = _demo_census()

    assert report.total_artists == expected_total
    assert report.enriched_artists == len(catalog)
    assert sum(report.by_gender.values()) == expected_total
    assert sum(report.by_basis.values()) == expected_total
    assert sum(report.fetched_at_age_days.values()) == expected_total


def test_the_sourced_unknown_split_matches_the_catalog() -> None:
    """Hand-computed against the demo catalog, not against the module's own output."""
    catalog = demo_catalog()
    unknown = sum(1 for a in catalog.values() if a.identity.gender is Gender.UNKNOWN)
    report = _demo_census()

    assert report.by_gender[str(Gender.UNKNOWN)] == unknown
    assert report.sourced == report.total_artists - unknown
    assert sum(report.unknown_reasons.values()) == unknown
    for gender in Gender:
        assert report.by_gender[str(gender)] == sum(
            1 for a in catalog.values() if a.identity.gender is gender
        )


def test_the_census_carries_no_artist_identifier() -> None:
    """Counts only. A per-artist export is the thing this must never become."""
    catalog = demo_catalog()
    rendered = _demo_census().to_json() + _demo_census().to_text()

    for artist in catalog.values():
        assert artist.artist_id not in rendered, "an artist id reached the census"
        assert artist.name not in rendered, "an artist name reached the census"
        for source in artist.identity.sources:
            assert source.citation not in rendered, "a citation URL reached the census"
    assert "http" not in rendered, "a URL of any kind has no place in an aggregate"


def test_a_world_with_nothing_enriched_says_so_rather_than_showing_zeros() -> None:
    """Done-when 4: 100% unknown with reason `never-enriched`, not an empty table."""
    known = ["never-seen-1", "never-seen-2", "never-seen-3"]
    report = census({}, as_of=_AS_OF, known_artist_ids=known)

    assert report.total_artists == 3
    assert report.enriched_artists == 0
    assert report.by_gender[str(Gender.UNKNOWN)] == 3
    assert report.unknown_fraction == 1.0
    assert report.unknown_reasons["never-enriched"] == 3
    assert report.unknown_reasons["no-permitted-claim"] == 0
    assert report.fetched_at_age_days[NEVER_FETCHED] == 3
    assert report.fetched_at_age_days[LINEAGE_NOT_RECORDED] == 0


def test_an_empty_world_reports_no_artists_rather_than_a_percentage() -> None:
    """Zero artists is not "0% sourced" — there is no population to have a share of."""
    report = census({}, as_of=_AS_OF)

    assert report.total_artists == 0
    assert report.unknown_fraction == 0.0
    assert "no artists" in report.to_text()
    assert "%" not in report.to_text()


def test_never_enriched_is_not_folded_into_an_age_bucket() -> None:
    """ "Never checked", "when is not recorded", and "checked long ago" are three facts."""
    catalog = demo_catalog()
    one = next(iter(catalog))
    report = census(
        {one: catalog[one]},
        as_of=_AS_OF,
        known_artist_ids=[one, "never-enriched-artist"],
        fetched_at={one: "2020-01-01"},
    )

    assert report.fetched_at_age_days["over-365"] == 1
    assert report.fetched_at_age_days[NEVER_FETCHED] == 1
    assert report.fetched_at_age_days[LINEAGE_NOT_RECORDED] == 0


def test_an_unparseable_lineage_date_is_not_reported_as_an_age() -> None:
    """A value the parser cannot read is absence, never `over-365`."""
    catalog = demo_catalog()
    one = next(iter(catalog))
    report = census(
        {one: catalog[one]}, as_of=_AS_OF, known_artist_ids=[one], fetched_at={one: "not-a-date"}
    )

    assert report.fetched_at_age_days[LINEAGE_NOT_RECORDED] == 1
    assert report.fetched_at_age_days["over-365"] == 0


def test_a_reason_the_cache_cannot_support_is_named_as_unsupported() -> None:
    """`upstream-unreachable` is real; the cache cannot tell it apart. Say so."""
    report = _demo_census()

    assert "upstream-unreachable" not in report.unknown_reasons
    assert "upstream-unreachable" in report.unsupported_unknown_reasons
    assert report.unsupported_unknown_reasons == dict(UNSUPPORTED_UNKNOWN_REASONS)
    assert "upstream-unreachable" in report.to_text()
    assert "#122" in report.to_text()


def test_a_filed_correction_moves_exactly_one_artist_and_is_counted(tmp_path) -> None:
    """Done-when 3, exercised through the real cache and the real resolver."""
    from pipeline.enrich import FixtureEnricher
    from pipeline.ingest import enrich_artist
    from pipeline.lastfm import FixtureLastfm

    lastfm = FixtureLastfm(scrobbles={}, tags={"mystery-act": ()}, similar={})
    enricher = FixtureEnricher(gender={}, composition={})
    cache = Cache(":memory:")
    try:
        before_artist = enrich_artist("mystery-act", "Mystery Act", lastfm, enricher, cache=cache)
        assert before_artist.identity.gender is Gender.UNKNOWN
        cache.put_artist(before_artist, fetched_at="2026-08-01")
        before = census(
            {"mystery-act": before_artist},
            as_of=_AS_OF,
            known_artist_ids=["mystery-act"],
            fetched_at={"mystery-act": "2026-08-01"},
        )

        cache.put_correction(
            "mystery-act",
            IdentityEvidence(
                kind=SourceKind.ARTIST_STATEMENT,
                value="nonbinary",
                citation="https://example.org/mystery-act-correction",
                retrieved_at="2026-07-01",
            ),
            entered_at="2026-07-01",
        )
        after_artist = enrich_artist("mystery-act", "Mystery Act", lastfm, enricher, cache=cache)
        after = census(
            {"mystery-act": after_artist},
            as_of=_AS_OF,
            known_artist_ids=["mystery-act"],
            fetched_at={"mystery-act": "2026-08-01"},
            local_correction_ids=[a for a, _ev, _at in cache.list_corrections()],
        )
    finally:
        cache.close()

    assert before.by_gender[str(Gender.UNKNOWN)] == 1
    assert before.with_local_correction == 0
    assert after.by_gender[str(Gender.UNKNOWN)] == 0
    assert after.by_gender[str(Gender.NONBINARY)] == 1
    assert after.with_local_correction == 1
    assert after.total_artists == before.total_artists == 1


def test_a_ledger_row_for_an_unknown_artist_is_not_counted_against_this_world() -> None:
    """A correction about an artist this world never heard of is not a fact about it."""
    report = _demo_census(local_correction_ids=["some-other-world-artist"])

    assert report.with_local_correction == 0


def test_the_committed_demo_census_matches_what_the_code_produces() -> None:
    """The same shape of gate `eval-check` and the committed render use.

    Regenerated at the committed `as_of` rather than today's date, so this pins
    the content without becoming a calendar bomb.
    """
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed["schema_version"] == SCHEMA_VERSION

    regenerated = _demo_census(as_of=committed["as_of"]).to_dict()

    assert regenerated == committed, "run `make census` and commit the result"


def test_the_committed_demo_census_names_no_artist() -> None:
    """The artifact is public and browsable, so the sentinel covers the file too."""
    text = COMMITTED.read_text(encoding="utf-8")
    for artist in demo_catalog().values():
        assert artist.artist_id not in text
        assert artist.name not in text
    assert DEMO_USER not in text
