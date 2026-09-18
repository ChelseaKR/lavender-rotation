"""Ingest orchestration: username -> stored profile + enriched catalog."""

from __future__ import annotations

import json
import logging

from pipeline.cache import Cache
from pipeline.enrich import FixtureEnricher
from pipeline.identity import IdentityEvidence, resolve_identity
from pipeline.ingest import (
    IdentityLabelChange,
    build_profile,
    diff_identity_sources,
    enrich_artist,
    ingest,
    refresh_catalog,
)
from pipeline.lastfm import FixtureLastfm, LastfmClient
from pipeline.models import FrontPerson, Gender, Orientation, Scrobble, SourceKind


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


# --- #93: the queer axis is an identity axis, on the refresh path too ---------
#
# `_identity_sources` looked at gender and band composition, and `_is_sourced`
# and `diff_identity_sources` both delegate to it. So an artist who came back
# from upstream carrying *only* a queer citation scored as "nothing came back":
# the citation was thrown away, `fetched_at` was not advanced, and the operator
# was told the artist was unconfirmed. That is exactly the conflation of silence
# with evidence that `RefreshOutcome`'s docstring says the type exists to
# refuse — inverted, on the axis `docs/audits/identity-data-ethics.md` calls the
# most dangerous one to get wrong.
#
# Artist names below are invented, for the reason tests/test_live_enrichment.py
# gives: a fixture is not a place to assert an orientation about a real person.

_P91_CITATION = "https://www.wikidata.org/wiki/Q000000001"


def _orientation_enricher(
    retrieved_at: str,
    value: str = "Q6649",
    *,
    artist_ids: tuple[str, ...] = ("unsourced-gender",),
) -> FixtureEnricher:
    """Upstream holds a P91 orientation claim and no gender claim at all."""
    return FixtureEnricher(
        gender={},
        composition={},
        orientation={
            artist_id: [
                IdentityEvidence(
                    kind=SourceKind.WIKIDATA_P91,
                    value=value,
                    citation=_P91_CITATION,
                    retrieved_at=retrieved_at,
                )
            ]
            for artist_id in artist_ids
        },
    )


def _queer_lastfm() -> FixtureLastfm:
    return FixtureLastfm(scrobbles={}, tags={"unsourced-gender": ()}, similar={})


def test_a_newly_sourced_orientation_is_written_not_discarded() -> None:
    """#93 case 1: upstream answered, so the run must not report silence."""
    lastfm = _queer_lastfm()
    cache = Cache(":memory:")
    try:
        # Cached with nothing sourced on any axis — the ordinary starting state
        # for an artist Wikidata had no P21 for at ingest time.
        blank = enrich_artist(
            "unsourced-gender",
            "Violet Meridian",
            lastfm,
            FixtureEnricher(gender={}, composition={}),
        )
        assert blank.identity.sources == ()
        assert blank.queer.sources == ()
        cache.put_artist(blank, fetched_at="2026-05-31")

        outcome = refresh_catalog(
            cache, lastfm, _orientation_enricher("2026-07-01"), fetched_at="2026-07-01"
        )

        assert outcome.verified == ("unsourced-gender",)
        assert outcome.unverified == ()
        assert outcome.upstream_answered, (
            "a citation came back over the wire; this run reached upstream"
        )
        stored = cache.get_artist("unsourced-gender")
        assert stored is not None
        assert stored.queer.orientation_sources, "the new citation must be persisted"
        assert cache.artist_fetched_at("unsourced-gender") == "2026-07-01"
    finally:
        cache.close()


def test_an_orientation_that_changed_upstream_reaches_the_change_log() -> None:
    """#93 case 2: the diff drives corrections reconciliation, so it must see it."""
    lastfm = _queer_lastfm()
    old = enrich_artist(
        "unsourced-gender", "Violet Meridian", lastfm, _orientation_enricher("2026-05-31")
    )
    new = enrich_artist(
        "unsourced-gender",
        "Violet Meridian",
        lastfm,
        _orientation_enricher("2026-07-01", value="Q43200"),  # bisexual
    )

    changes = diff_identity_sources(old, new)

    assert changes == [
        IdentityLabelChange(
            artist_id="unsourced-gender",
            source_kind="wikidata-p91",
            old_value="Q6649",
            new_value="Q43200",
            retrieved_at="2026-07-01",
        )
    ]


def test_a_queer_axis_change_can_reconcile_a_filed_correction(tmp_path) -> None:
    """The whole point of the diff: a pending row on this axis can now close.

    `pipeline.corrections.reconcile` keys on ``(artist_id, source_kind)`` taken
    from the change list, so before #93 a person could file a `wikidata-p91`
    correction, watch upstream adopt it, and run `lavender refresh` forever
    without the row ever leaving `still_open`.
    """
    from pipeline.corrections import add_correction, reconcile

    ledger = tmp_path / "pending-corrections.json"
    add_correction(
        ledger,
        artist_id="unsourced-gender",
        source_kind="wikidata-p91",
        current_value="Q6649",
        proposed_value="Q43200",
        citation=_P91_CITATION,
        note="upstream records the wrong orientation",
        filed_at="2026-06-01",
    )
    lastfm = _queer_lastfm()
    old = enrich_artist(
        "unsourced-gender", "Violet Meridian", lastfm, _orientation_enricher("2026-05-31")
    )
    new = enrich_artist(
        "unsourced-gender",
        "Violet Meridian",
        lastfm,
        _orientation_enricher("2026-07-01", value="Q43200"),
    )

    outcome = reconcile(ledger, diff_identity_sources(old, new), observed_at="2026-07-01")

    assert [row.source_kind for row in outcome.reconciled] == ["wikidata-p91"]
    assert outcome.still_open == ()


def test_a_gender_statement_and_an_orientation_statement_are_not_the_same_claim() -> None:
    """Two `artist-statement` citations answer two different questions.

    Matching sources by kind alone made them each other's "old value", so a
    stable gender statement and a stable orientation statement diffed as a
    change in both directions. They are matched on (kind, citation) now.
    """
    lastfm = _queer_lastfm()
    enricher = FixtureEnricher(
        gender={
            "unsourced-gender": [
                IdentityEvidence(
                    kind=SourceKind.ARTIST_STATEMENT,
                    value="woman",
                    citation="https://example.invalid/gender-interview",
                    retrieved_at="2026-05-31",
                )
            ]
        },
        composition={},
        orientation={
            "unsourced-gender": [
                IdentityEvidence(
                    kind=SourceKind.ARTIST_STATEMENT,
                    value="lesbian",
                    citation="https://example.invalid/orientation-interview",
                    retrieved_at="2026-05-31",
                )
            ]
        },
    )
    artist = enrich_artist("unsourced-gender", "Violet Meridian", lastfm, enricher)
    assert artist.identity.gender is Gender.WOMAN
    assert artist.queer.orientation.value == "lesbian"

    assert diff_identity_sources(artist, artist) == []


def test_one_source_that_answers_two_questions_is_counted_once() -> None:
    """A P21 claim of "trans woman" is both the gender and the trans citation."""
    from pipeline.ingest import _identity_sources

    lastfm = _queer_lastfm()
    enricher = FixtureEnricher(
        gender={
            "unsourced-gender": [
                IdentityEvidence(
                    kind=SourceKind.WIKIDATA_P21,
                    value="Q1052281",  # trans woman
                    citation="https://www.wikidata.org/wiki/Q000000002",
                    retrieved_at="2026-05-31",
                )
            ]
        },
        composition={},
    )
    artist = enrich_artist("unsourced-gender", "Violet Meridian", lastfm, enricher)

    assert artist.identity.gender is Gender.WOMAN  # ADR 0011: no cis/trans split
    assert artist.queer.trans_self_identified is True
    assert len(_identity_sources(artist)) == 1


def test_a_partial_answer_never_erases_the_axis_it_was_silent_about() -> None:
    """#90's bug, one axis down — and reachable through an ordinary outage.

    `MusicBrainzEnricher` reads a gender claim straight out of the MusicBrainz
    payload, but a P91 orientation claim needs a *second* fetch, of the Wikidata
    entity, and `_json` renders any failure there as `None`. So a Wikidata
    outage while MusicBrainz is up produces exactly this shape: an artist who
    comes back carrying a gender citation and nothing on the queer axis.

    Whole-artist protection is not enough for that. `_is_sourced(refreshed)` is
    true — upstream did answer — so the refreshed artist was written over the
    cached one, taking the P91 citation with it. And `diff_identity_sources`
    walks the *new* sources, so an empty axis has nothing to report: erasure,
    plus a clean bill of health. That is the sentence this branch exists to make
    true, applied per axis instead of per artist.
    """
    lastfm = FixtureLastfm(scrobbles={}, tags={"fixture-artist": ()}, similar={})
    cache = Cache(":memory:")
    try:
        both = enrich_artist(
            "fixture-artist",
            "Fixture Artist",
            lastfm,
            FixtureEnricher(
                gender={
                    "fixture-artist": [
                        IdentityEvidence(
                            kind=SourceKind.MUSICBRAINZ_GENDER,
                            value="female",
                            citation="https://musicbrainz.org/artist/"
                            "b7ffd2af-418f-4be2-bdd1-22f8b48613da",
                            retrieved_at="2026-05-31",
                        )
                    ]
                },
                composition={},
                orientation={
                    "fixture-artist": [
                        IdentityEvidence(
                            kind=SourceKind.WIKIDATA_P91,
                            value="Q6649",
                            citation="https://www.wikidata.org/wiki/Q11111111",
                            retrieved_at="2026-05-31",
                        )
                    ]
                },
            ),
        )
        assert both.identity.gender is Gender.WOMAN
        assert both.queer.orientation is Orientation.LESBIAN
        cache.put_artist(both, fetched_at="2026-05-31")

        # MusicBrainz answers; Wikidata does not.
        gender_only = FixtureEnricher(
            gender={
                "fixture-artist": [
                    IdentityEvidence(
                        kind=SourceKind.MUSICBRAINZ_GENDER,
                        value="female",
                        citation="https://musicbrainz.org/artist/"
                        "b7ffd2af-418f-4be2-bdd1-22f8b48613da",
                        retrieved_at="2026-07-01",
                    )
                ]
            },
            composition={},
        )
        outcome = refresh_catalog(cache, lastfm, gender_only, fetched_at="2026-07-01")

        stored = cache.get_artist("fixture-artist")
        assert stored is not None
        # The axis upstream answered about moves.
        assert stored.identity.gender is Gender.WOMAN
        assert stored.identity.sources[0].retrieved_at == "2026-07-01"
        # The axis it was silent about does not.
        assert stored.queer.orientation is Orientation.LESBIAN, (
            "a silent Wikidata erased a citation the operator's ingest paid for"
        )
        assert stored.queer.orientation_sources[0].retrieved_at == "2026-05-31", (
            "the preserved claim's own lineage date must not be moved by a run "
            "that never re-read it"
        )
        # And the run says so rather than reporting a clean pass.
        assert outcome.verified == ("fixture-artist",)
        assert outcome.protected == ("fixture-artist",)
    finally:
        cache.close()


def test_the_same_protection_covers_the_composition_axis() -> None:
    """All three axes, or the guard only covers the one it was written for."""
    lastfm = FixtureLastfm(scrobbles={}, tags={"fixture-band": ()}, similar={})
    front = FrontPerson(
        name="Fixture Front",
        role="lead vocals",
        identity=resolve_identity(
            [
                IdentityEvidence(
                    kind=SourceKind.ARTIST_STATEMENT,
                    value="woman",
                    citation="https://example.org/fixture-front",
                    retrieved_at="2026-05-31",
                )
            ]
        ),
    )
    lineup_evidence = [
        IdentityEvidence(
            kind=SourceKind.DISCOGS_LINEUP,
            value="lineup",
            citation="https://www.discogs.com/artist/1234567-Fixture-Band",
            retrieved_at="2026-05-31",
        )
    ]
    cache = Cache(":memory:")
    try:
        band = enrich_artist(
            "fixture-band",
            "Fixture Band",
            lastfm,
            FixtureEnricher(
                gender={},
                composition={"fixture-band": ([front], lineup_evidence)},
                orientation={
                    "fixture-band": [
                        IdentityEvidence(
                            kind=SourceKind.WIKIDATA_P91,
                            value="Q6649",
                            citation="https://www.wikidata.org/wiki/Q11111111",
                            retrieved_at="2026-05-31",
                        )
                    ]
                },
            ),
        )
        assert band.composition is not None
        assert band.sourced_front_genders == frozenset({Gender.WOMAN})
        cache.put_artist(band, fetched_at="2026-05-31")

        # Discogs goes quiet; the orientation lookup still answers.
        orientation_only = _orientation_enricher("2026-07-01", artist_ids=("fixture-band",))
        outcome = refresh_catalog(cache, lastfm, orientation_only, fetched_at="2026-07-01")

        stored = cache.get_artist("fixture-band")
        assert stored is not None
        assert stored.composition is not None, "a silent lineup source erased the composition"
        assert stored.sourced_front_genders == frozenset({Gender.WOMAN})
        assert stored.composition.sources[0].retrieved_at == "2026-05-31"
        assert stored.queer.orientation is Orientation.LESBIAN
        assert outcome.protected == ("fixture-band",)
    finally:
        cache.close()


def test_an_axis_that_did_answer_is_still_written() -> None:
    """The over-correction this guard must not become.

    A protection that kept every cached axis would pass every erasure test in
    this file by never writing anything, which is the same defect in the other
    direction: a refresh that cannot refresh. An axis that comes back carrying a
    *different* value has to move.
    """
    lastfm = FixtureLastfm(scrobbles={}, tags={"fixture-artist": ()}, similar={})
    cache = Cache(":memory:")
    try:
        was = enrich_artist(
            "fixture-artist",
            "Fixture Artist",
            lastfm,
            _orientation_enricher("2026-05-31", artist_ids=("fixture-artist",)),
        )
        assert was.queer.orientation is Orientation.LESBIAN
        cache.put_artist(was, fetched_at="2026-05-31")

        outcome = refresh_catalog(
            cache,
            lastfm,
            _orientation_enricher("2026-07-01", value="Q43200", artist_ids=("fixture-artist",)),
            fetched_at="2026-07-01",
        )

        stored = cache.get_artist("fixture-artist")
        assert stored is not None
        assert stored.queer.orientation is Orientation.BISEXUAL
        assert outcome.protected == ()
        assert [(c.source_kind, c.old_value, c.new_value) for c in outcome.changes] == [
            ("wikidata-p91", "Q6649", "Q43200")
        ]
    finally:
        cache.close()


# --- #112: the local corrections ledger is not upstream speaking --------------
#
# `enrich_artist` is called with `cache=cache` on the refresh path, so
# `Cache.get_corrections` merges the operator's ledger into the evidence and the
# resolvers turn it into ordinary `Source` rows. `_is_sourced` asked only
# whether *any* source existed, so a corrected artist came back "sourced" on a
# run where every upstream lookup timed out: the cached upstream citations were
# written over, `fetched_at` advanced, `diff_identity_sources` walked the new
# sources and found nothing to report, and `upstream_answered` — documented as
# "only a citation coming back over the wire proves the far end spoke" — was
# True. The blast radius was exactly the artists someone cared enough to
# correct.

_MB_CITATION = "https://musicbrainz.org/artist/b7ffd2af-418f-4be2-bdd1-22f8b48613da"
_CORRECTION_CITATION = "https://example.org/mystery-act-correction"


def _dead_upstream() -> FixtureEnricher:
    """What `MusicBrainzEnricher` produces on a timeout, a 503 or a throttle.

    `_json` renders every failure as `None`, so all three evidence methods come
    back empty — indistinguishable, at this layer, from "upstream holds no
    claim".
    """
    return FixtureEnricher(gender={}, composition={}, orientation={})


def _cached_with_a_correction(
    cache: Cache,
    lastfm: FixtureLastfm,
    upstream: FixtureEnricher,
    *,
    correction_value: str,
    fetched_at: str = "2026-08-01",
) -> None:
    """Cache one artist carrying an upstream citation *and* a filed correction."""
    cache.put_correction(
        "mystery-act",
        IdentityEvidence(
            kind=SourceKind.ARTIST_STATEMENT,
            value=correction_value,
            citation=_CORRECTION_CITATION,
            retrieved_at="2026-07-01",
        ),
        entered_at="2026-07-01",
    )
    cached = enrich_artist("mystery-act", "Mystery Act", lastfm, upstream, cache=cache)
    cache.put_artist(cached, fetched_at=fetched_at)


def test_a_correction_only_refresh_is_not_upstream_speaking() -> None:
    """#112: a dead upstream must not erase the citation the ingest paid for."""
    lastfm = FixtureLastfm(scrobbles={}, tags={"mystery-act": ()}, similar={})
    upstream = FixtureEnricher(
        gender={
            "mystery-act": [
                IdentityEvidence(
                    kind=SourceKind.MUSICBRAINZ_GENDER,
                    value="female",
                    citation=_MB_CITATION,
                    retrieved_at="2026-08-01",
                )
            ]
        },
        composition={},
    )
    cache = Cache(":memory:")
    try:
        _cached_with_a_correction(cache, lastfm, upstream, correction_value="nonbinary")
        before = cache.get_artist("mystery-act")
        assert before is not None
        assert before.identity.gender is Gender.NONBINARY  # the correction wins
        assert any(s.kind is SourceKind.MUSICBRAINZ_GENDER for s in before.identity.sources)

        outcome = refresh_catalog(cache, lastfm, _dead_upstream(), fetched_at="2026-09-06")

        assert outcome.verified == ()
        assert outcome.unverified == ("mystery-act",)
        assert outcome.protected == ("mystery-act",)
        assert outcome.upstream_answered is False, (
            "a correction read out of the local ledger is not evidence that the far end spoke"
        )
        assert outcome.changes == ()

        stored = cache.get_artist("mystery-act")
        assert stored is not None
        assert any(s.kind is SourceKind.MUSICBRAINZ_GENDER for s in stored.identity.sources), (
            "a run that never reached MusicBrainz erased its citation"
        )
        assert stored.identity.conflict is True, "the FIX-10 source disagreement must survive"
        assert cache.artist_fetched_at("mystery-act") == "2026-08-01", (
            "`fetched_at` claims this artist was checked that day; nobody answered"
        )
    finally:
        cache.close()


def test_a_correction_does_not_stand_in_for_a_p91_read_on_the_queer_axis() -> None:
    """#112, ADR 0011's axis: a trans self-identification is not a P91 fetch."""
    lastfm = FixtureLastfm(scrobbles={}, tags={"mystery-act": ()}, similar={})
    upstream = FixtureEnricher(
        gender={},
        composition={},
        orientation={
            "mystery-act": [
                IdentityEvidence(
                    kind=SourceKind.WIKIDATA_P91,
                    value="Q6636",
                    citation="https://www.wikidata.org/wiki/Q22222222",
                    retrieved_at="2026-08-01",
                )
            ]
        },
    )
    cache = Cache(":memory:")
    try:
        # "trans woman" is in `_TRANS_ASSERTED_VALUES`, so the correction lands
        # on the queer axis as well as the gender one.
        _cached_with_a_correction(cache, lastfm, upstream, correction_value="trans woman")
        before = cache.get_artist("mystery-act")
        assert before is not None
        assert before.queer.trans_self_identified is True
        assert any(s.kind is SourceKind.WIKIDATA_P91 for s in before.queer.sources)

        outcome = refresh_catalog(cache, lastfm, _dead_upstream(), fetched_at="2026-09-06")

        assert outcome.upstream_answered is False
        assert outcome.protected == ("mystery-act",)
        stored = cache.get_artist("mystery-act")
        assert stored is not None
        assert any(s.kind is SourceKind.WIKIDATA_P91 for s in stored.queer.sources), (
            "the P91 citation was erased by a run that never read Wikidata"
        )
        assert cache.artist_fetched_at("mystery-act") == "2026-08-01"
    finally:
        cache.close()


def test_a_correction_does_not_defeat_the_per_axis_guard_on_a_partial_answer() -> None:
    """#112 meets #96: MusicBrainz up, Wikidata down, and a correction filed.

    The per-axis guard asked whether the refreshed axis was *empty*. A ledger
    correction is on every axis it speaks to, on every run, so the queer axis
    was never empty and the guard never fired — the P91 citation went out with
    the write that #96 exists to prevent.
    """
    lastfm = FixtureLastfm(scrobbles={}, tags={"mystery-act": ()}, similar={})
    both_axes = FixtureEnricher(
        gender={
            "mystery-act": [
                IdentityEvidence(
                    kind=SourceKind.MUSICBRAINZ_GENDER,
                    value="female",
                    citation=_MB_CITATION,
                    retrieved_at="2026-08-01",
                )
            ]
        },
        composition={},
        orientation={
            "mystery-act": [
                IdentityEvidence(
                    kind=SourceKind.WIKIDATA_P91,
                    value="Q6636",
                    citation="https://www.wikidata.org/wiki/Q22222222",
                    retrieved_at="2026-08-01",
                )
            ]
        },
    )
    gender_only = FixtureEnricher(
        gender={
            "mystery-act": [
                IdentityEvidence(
                    kind=SourceKind.MUSICBRAINZ_GENDER,
                    value="female",
                    citation=_MB_CITATION,
                    retrieved_at="2026-09-06",
                )
            ]
        },
        composition={},
    )
    cache = Cache(":memory:")
    try:
        _cached_with_a_correction(cache, lastfm, both_axes, correction_value="trans woman")

        outcome = refresh_catalog(cache, lastfm, gender_only, fetched_at="2026-09-06")

        assert outcome.verified == ("mystery-act",)  # MusicBrainz did answer
        assert outcome.protected == ("mystery-act",), (
            "the axis Wikidata was silent about must be reported as held"
        )
        stored = cache.get_artist("mystery-act")
        assert stored is not None
        assert any(s.kind is SourceKind.WIKIDATA_P91 for s in stored.queer.sources), (
            "a silent Wikidata erased a P91 citation because a correction filled the axis"
        )
        # The axis that did answer still moves.
        gender_sources = [
            s for s in stored.identity.sources if s.kind is SourceKind.MUSICBRAINZ_GENDER
        ]
        assert gender_sources and gender_sources[0].retrieved_at == "2026-09-06"
    finally:
        cache.close()


def test_a_corrected_artist_upstream_did_answer_about_is_still_verified() -> None:
    """The over-correction this must not become: corrections are not poison.

    Ignoring a correction as *proof* must not make a corrected artist
    unrefreshable. Upstream answering about a corrected artist is still an
    answer, and the correction still wins at resolve time.
    """
    lastfm = FixtureLastfm(scrobbles={}, tags={"mystery-act": ()}, similar={})
    was = FixtureEnricher(
        gender={
            "mystery-act": [
                IdentityEvidence(
                    kind=SourceKind.MUSICBRAINZ_GENDER,
                    value="female",
                    citation=_MB_CITATION,
                    retrieved_at="2026-08-01",
                )
            ]
        },
        composition={},
    )
    now = FixtureEnricher(
        gender={
            "mystery-act": [
                IdentityEvidence(
                    kind=SourceKind.MUSICBRAINZ_GENDER,
                    value="male",
                    citation=_MB_CITATION,
                    retrieved_at="2026-09-06",
                )
            ]
        },
        composition={},
    )
    cache = Cache(":memory:")
    try:
        _cached_with_a_correction(cache, lastfm, was, correction_value="nonbinary")

        outcome = refresh_catalog(cache, lastfm, now, fetched_at="2026-09-06")

        assert outcome.verified == ("mystery-act",)
        assert outcome.upstream_answered is True
        assert cache.artist_fetched_at("mystery-act") == "2026-09-06"
        assert [(c.source_kind, c.old_value, c.new_value) for c in outcome.changes] == [
            ("musicbrainz-gender", "female", "male")
        ]
        stored = cache.get_artist("mystery-act")
        assert stored is not None
        assert stored.identity.gender is Gender.NONBINARY, "the correction still wins"
    finally:
        cache.close()
