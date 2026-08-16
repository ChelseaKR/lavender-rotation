"""Ingest orchestration: username -> stored profile + enriched catalog."""

from __future__ import annotations

import json
import logging

from pipeline.cache import Cache
from pipeline.enrich import FixtureEnricher
from pipeline.identity import IdentityEvidence
from pipeline.ingest import (
    IdentityLabelChange,
    build_profile,
    diff_identity_sources,
    enrich_artist,
    ingest,
    refresh_catalog,
)
from pipeline.lastfm import FixtureLastfm, LastfmClient
from pipeline.models import Gender, Scrobble, SourceKind


def test_build_profile_counts_plays(scrobbles, demo_user) -> None:
    profile = build_profile(demo_user, scrobbles)
    assert profile.play_counts["mitski"] == 10
    assert profile.top_artists(1) == ["mitski"]


def test_enrich_artist_resolves_sourced_identity(source, enricher) -> None:
    artist = enrich_artist("mitski", "Mitski", source, enricher)
    assert artist.identity.gender is Gender.WOMAN
    assert artist.identity.sources
    assert "indie rock" in artist.tags


def test_enrich_artist_defaults_to_unknown(source, enricher) -> None:
    artist = enrich_artist("mystery-act", "Mystery Act", source, enricher)
    assert artist.identity.gender is Gender.UNKNOWN
    assert artist.composition is None


def test_ingest_persists_to_cache(demo_user, source, enricher) -> None:
    cache = Cache(":memory:")
    try:
        profile, _catalog = ingest(
            demo_user, source, enricher, cache=cache, fetched_at="2026-05-31"
        )
        assert profile.play_counts
        # listened artists are enriched + cached with a lineage timestamp
        assert cache.get_artist("mitski") is not None
        assert cache.artist_fetched_at("mitski") == "2026-05-31"
        assert cache.get_scrobbles(demo_user)
        # tags are attached back onto the profile after enrichment
        assert profile.tags["mitski"]
    finally:
        cache.close()


def test_ingest_without_cache_still_returns_catalog(demo_user, source, enricher) -> None:
    profile, catalog = ingest(demo_user, source, enricher)
    assert set(catalog) <= set(profile.play_counts)
    assert "mitski" in catalog


# --- refresh / IdentityLabelChange (EXP-05's "fix it at the source" round-trip) --


def _lastfm() -> FixtureLastfm:
    return FixtureLastfm(scrobbles={}, tags={"mitski": ()}, similar={})


def _wikidata_enricher(retrieved_at: str) -> FixtureEnricher:
    return FixtureEnricher(
        gender={
            "mitski": [
                IdentityEvidence(
                    kind=SourceKind.WIKIDATA_P21,
                    value="Q6581072",
                    citation="https://www.wikidata.org/wiki/Q16735549",
                    retrieved_at=retrieved_at,
                )
            ]
        },
        composition={},
    )


def test_diff_identity_sources_reports_a_moved_retrieved_at() -> None:
    lastfm = _lastfm()
    old = enrich_artist("mitski", "Mitski", lastfm, _wikidata_enricher("2026-05-31"))
    new = enrich_artist("mitski", "Mitski", lastfm, _wikidata_enricher("2026-07-01"))

    changes = diff_identity_sources(old, new)

    assert changes == [
        IdentityLabelChange(
            artist_id="mitski",
            source_kind="wikidata-p21",
            old_value="Q6581072",
            new_value="Q6581072",
            retrieved_at="2026-07-01",
        )
    ]


def test_diff_identity_sources_is_empty_when_nothing_changed() -> None:
    lastfm = _lastfm()
    enricher = _wikidata_enricher("2026-05-31")
    a = enrich_artist("mitski", "Mitski", lastfm, enricher)
    b = enrich_artist("mitski", "Mitski", lastfm, enricher)
    assert diff_identity_sources(a, b) == []


def test_refresh_catalog_updates_cache_lineage_and_returns_changes() -> None:
    lastfm = _lastfm()
    cache = Cache(":memory:")
    try:
        artist = enrich_artist(
            "mitski",
            "Mitski",
            lastfm,
            _wikidata_enricher("2026-05-31"),
            listeners=1_200_000,
            playcount=42,
        )
        cache.put_artist(artist, fetched_at="2026-05-31")

        changes = refresh_catalog(
            cache, lastfm, _wikidata_enricher("2026-07-01"), fetched_at="2026-07-01"
        )

        assert len(changes) == 1
        assert changes[0].artist_id == "mitski"
        assert changes[0].retrieved_at == "2026-07-01"
        assert cache.artist_fetched_at("mitski") == "2026-07-01"
        refreshed = cache.get_artist("mitski")
        assert refreshed is not None
        assert refreshed.listeners == 1_200_000  # popularity preserved across refresh
    finally:
        cache.close()


def test_refresh_catalog_on_empty_cache_is_a_noop() -> None:
    cache = Cache(":memory:")
    try:
        changes = refresh_catalog(
            cache, _lastfm(), _wikidata_enricher("2026-07-01"), fetched_at="2026-07-01"
        )
        assert changes == []
    finally:
        cache.close()


def test_cache_list_artist_ids_reflects_puts() -> None:
    cache = Cache(":memory:")
    try:
        assert cache.list_artist_ids() == []
        artist = enrich_artist("mitski", "Mitski", _lastfm(), _wikidata_enricher("2026-05-31"))
        cache.put_artist(artist, fetched_at="2026-05-31")
        assert cache.list_artist_ids() == ["mitski"]
    finally:
        cache.close()


class _RecordingFixtureLastfm(FixtureLastfm):
    def __init__(self, history: list[Scrobble], username: str) -> None:
        super().__init__(scrobbles={username: history}, tags={}, similar={})
        self.since_calls: list[int] = []

    def scrobbles_since(
        self, username: str, since_ts: int = 0, page_size: int = 200
    ) -> list[Scrobble]:
        self.since_calls.append(since_ts)
        return super().scrobbles_since(username, since_ts=since_ts, page_size=page_size)


def _history(count: int, start: int = 1_000_000) -> list[Scrobble]:
    return [Scrobble("mitski", "Mitski", f"track-{i}", start + i) for i in range(count)]


def test_incremental_first_sync_loads_full_history(demo_user, enricher) -> None:
    history = _history(450)
    source = FixtureLastfm(scrobbles={demo_user: history}, tags={}, similar={})
    with Cache(":memory:") as cache:
        profile, _ = ingest(demo_user, source, enricher, cache=cache, limit=50)
        assert profile.play_counts["mitski"] == 450
        assert cache.last_synced_ts(demo_user) == history[-1].ts


def test_incremental_second_sync_uses_watermark(demo_user, enricher) -> None:
    first = _history(300)
    source = _RecordingFixtureLastfm(first, demo_user)
    with Cache(":memory:") as cache:
        ingest(demo_user, source, enricher, cache=cache, limit=100)
        watermark = cache.last_synced_ts(demo_user)
        source._scrobbles[demo_user] = first + _history(20, watermark + 1000)
        profile, _ = ingest(demo_user, source, enricher, cache=cache, limit=100)
        assert source.since_calls == [0, watermark]
        assert profile.play_counts["mitski"] == 320


def test_incremental_repeated_sync_is_idempotent(demo_user, enricher) -> None:
    source = FixtureLastfm(scrobbles={demo_user: _history(120)}, tags={}, similar={})
    with Cache(":memory:") as cache:
        first, _ = ingest(demo_user, source, enricher, cache=cache, limit=40)
        second, _ = ingest(demo_user, source, enricher, cache=cache, limit=40)
        assert second.play_counts == first.play_counts
        assert len(cache.get_scrobbles(demo_user)) == 120


class _PagedLastfmClient(LastfmClient):
    def __init__(self) -> None:
        self.pages: list[int] = []

    def _get(self, params: dict[str, str]) -> str:
        page = int(params["page"])
        self.pages.append(page)
        timestamps = [101, 100] if page == 1 else [103, 102]
        tracks = [
            {
                "artist": {"#text": "Mitski", "mbid": "mitski"},
                "name": f"track-{ts}",
                "date": {"uts": str(ts)},
            }
            for ts in timestamps
        ]
        return json.dumps({"recenttracks": {"track": tracks, "@attr": {"totalPages": "2"}}})


def test_live_client_drains_pages_and_filters_watermark() -> None:
    client = _PagedLastfmClient()
    result = client.scrobbles_since("listener", since_ts=100, page_size=2)
    assert client.pages == [1, 2]
    assert [scrobble.ts for scrobble in result] == [101, 102, 103]


def test_ingest_emits_local_stage_summary(caplog, demo_user, source, enricher) -> None:
    logger = logging.getLogger("lavender.ingest")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="lavender.ingest"):
            ingest(demo_user, source, enricher)
    finally:
        logger.removeHandler(caplog.handler)
    messages = [record.getMessage() for record in caplog.records]
    assert any("stage=ingest event=start" in message for message in messages)
    assert any("stage=ingest event=end" in message for message in messages)
