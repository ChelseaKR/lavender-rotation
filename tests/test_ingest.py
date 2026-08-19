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


def _wikidata_enricher(
    retrieved_at: str, artist_ids: tuple[str, ...] = ("mitski",)
) -> FixtureEnricher:
    return FixtureEnricher(
        gender={
            artist_id: [
                IdentityEvidence(
                    kind=SourceKind.WIKIDATA_P21,
                    value="Q6581072",
                    citation="https://www.wikidata.org/wiki/Q16735549",
                    retrieved_at=retrieved_at,
                )
            ]
            for artist_id in artist_ids
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

        outcome = refresh_catalog(
            cache, lastfm, _wikidata_enricher("2026-07-01"), fetched_at="2026-07-01"
        )

        assert outcome.verified == ("mitski",)
        assert outcome.upstream_answered
        assert len(outcome.changes) == 1
        assert outcome.changes[0].artist_id == "mitski"
        assert outcome.changes[0].retrieved_at == "2026-07-01"
        assert cache.artist_fetched_at("mitski") == "2026-07-01"
        refreshed = cache.get_artist("mitski")
        assert refreshed is not None
        assert refreshed.listeners == 1_200_000  # popularity preserved across refresh
    finally:
        cache.close()


def test_refresh_catalog_on_empty_cache_reports_nothing_attempted_not_a_clean_pass() -> None:
    """An empty cache must not read as "checked everything, all agreed"."""
    cache = Cache(":memory:")
    try:
        outcome = refresh_catalog(
            cache, _lastfm(), _wikidata_enricher("2026-07-01"), fetched_at="2026-07-01"
        )
        assert outcome.attempted == 0
        assert outcome.changes == ()
        # The distinguishing bit: nothing was verified, so nothing may claim to
        # have consulted upstream.
        assert not outcome.upstream_answered
        assert "no cached artists" in outcome.summary_line()
    finally:
        cache.close()


def test_refresh_can_be_bounded_to_named_artists() -> None:
    """A whole-catalog walk is many runs against a ~1 req/s upstream."""
    lastfm = _lastfm()
    cache = Cache(":memory:")
    try:
        for artist_id in ("mitski", "other"):
            cache.put_artist(
                enrich_artist(artist_id, artist_id, lastfm, _wikidata_enricher("2026-05-31")),
                fetched_at="2026-05-31",
            )
        outcome = refresh_catalog(
            cache,
            lastfm,
            _wikidata_enricher("2026-07-01"),
            fetched_at="2026-07-01",
            artist_ids=["mitski"],
        )
        assert outcome.attempted == 1
        assert outcome.verified == ("mitski",)
        # the untouched artist keeps its original lineage date
        assert cache.artist_fetched_at("other") == "2026-05-31"
    finally:
        cache.close()


# -- the refresh path must never render an unread upstream as agreement ----------


class _DeadEnricher:
    """Every lookup comes back empty — what `MusicBrainzEnricher` returns when
    the network is down, since it renders a fetch failure as "no evidence"."""

    def gender_evidence(self, artist_id: str) -> list[IdentityEvidence]:
        return []

    def orientation_evidence(self, artist_id: str) -> list[IdentityEvidence]:
        return []

    def composition_evidence(self, artist_id: str) -> tuple[list[object], list[IdentityEvidence]]:
        return [], []


class _RaisingEnricher:
    """Every lookup raises — an upstream that errors instead of going quiet."""

    def gender_evidence(self, artist_id: str) -> list[IdentityEvidence]:
        raise RuntimeError("upstream exploded")

    def orientation_evidence(self, artist_id: str) -> list[IdentityEvidence]:
        raise RuntimeError("upstream exploded")

    def composition_evidence(self, artist_id: str) -> tuple[list[object], list[IdentityEvidence]]:
        raise RuntimeError("upstream exploded")


def test_a_silent_upstream_never_erases_a_cited_identity() -> None:
    """The regression this whole type exists for.

    A refresh against a dead upstream used to write an evidence-free ``Artist``
    over every cited one and report zero changes doing it — because the diff
    walks the *new* sources, and there were none. Erasure plus a clean bill of
    health.
    """
    lastfm = _lastfm()
    cache = Cache(":memory:")
    try:
        sourced = enrich_artist("mitski", "Mitski", lastfm, _wikidata_enricher("2026-05-31"))
        assert sourced.identity.gender is Gender.WOMAN
        cache.put_artist(sourced, fetched_at="2026-05-31")

        outcome = refresh_catalog(cache, lastfm, _DeadEnricher(), fetched_at="2026-07-01")

        # The citation survives, unmodified, with its original lineage date —
        # `fetched_at` claims the artist was checked that day, and nobody answered.
        kept = cache.get_artist("mitski")
        assert kept is not None
        assert kept.identity.gender is Gender.WOMAN
        assert kept.identity.sources
        assert cache.artist_fetched_at("mitski") == "2026-05-31"

        # And the report says so, rather than "no identity-label changes".
        assert outcome.protected == ("mitski",)
        assert outcome.unverified == ("mitski",)
        assert outcome.verified == ()
        assert not outcome.upstream_answered
        assert "unreachable" in outcome.summary_line()
        assert "nothing was rewritten" in outcome.summary_line()
    finally:
        cache.close()


def test_upstream_answered_is_proof_of_a_citation_not_merely_of_trying() -> None:
    """A run where every lookup came back empty tried hard and learned nothing."""
    lastfm = _lastfm()
    cache = Cache(":memory:")
    try:
        # An artist with no citation to begin with: nothing is at risk, so it is
        # `unverified` but not `protected`. It still may not prove upstream spoke.
        cache.put_artist(
            enrich_artist("mystery-act", "Mystery Act", lastfm, _DeadEnricher()),
            fetched_at="2026-05-31",
        )
        outcome = refresh_catalog(cache, lastfm, _DeadEnricher(), fetched_at="2026-07-01")
        assert outcome.attempted == 1
        assert outcome.unverified == ("mystery-act",)
        assert outcome.protected == ()
        assert not outcome.upstream_answered
    finally:
        cache.close()


def test_one_exploding_artist_costs_that_artist_not_the_run() -> None:
    lastfm = _lastfm()
    cache = Cache(":memory:")
    try:
        seed = _wikidata_enricher("2026-05-31", artist_ids=("mitski", "boom"))
        for artist_id in ("mitski", "boom"):
            cache.put_artist(
                enrich_artist(artist_id, artist_id, lastfm, seed), fetched_at="2026-05-31"
            )

        class _OnlyBoomRaises(FixtureEnricher):
            def gender_evidence(self, artist_id: str) -> list[IdentityEvidence]:
                if artist_id == "boom":
                    raise RuntimeError("upstream exploded")
                return super().gender_evidence(artist_id)

        fresh = _wikidata_enricher("2026-07-01", artist_ids=("mitski", "boom"))
        enricher = _OnlyBoomRaises(gender=fresh._gender, composition={})
        outcome = refresh_catalog(cache, lastfm, enricher, fetched_at="2026-07-01")

        assert outcome.attempted == 2
        assert outcome.verified == ("mitski",)
        assert outcome.failed == ("boom",)
        assert outcome.upstream_answered  # one real citation came back
        assert "errored" in outcome.summary_line()
        # the exploded artist keeps everything it had
        boom = cache.get_artist("boom")
        assert boom is not None and boom.identity.sources
        assert cache.artist_fetched_at("boom") == "2026-05-31"
    finally:
        cache.close()


def test_every_lookup_raising_is_not_upstream_agreement() -> None:
    lastfm = _lastfm()
    cache = Cache(":memory:")
    try:
        cache.put_artist(
            enrich_artist("mitski", "Mitski", lastfm, _wikidata_enricher("2026-05-31")),
            fetched_at="2026-05-31",
        )
        outcome = refresh_catalog(cache, lastfm, _RaisingEnricher(), fetched_at="2026-07-01")
        assert outcome.failed == ("mitski",)
        assert outcome.changes == ()
        assert not outcome.upstream_answered
        kept = cache.get_artist("mitski")
        assert kept is not None and kept.identity.gender is Gender.WOMAN
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
