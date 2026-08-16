"""Cache + serialisation: round-trip fidelity, lineage timestamps, guardrail on load."""

from __future__ import annotations

import pytest
from pipeline.cache import CACHE_SCHEMA_VERSION, Cache
from pipeline.models import (
    Artist,
    Gender,
    IdentityBasis,
    IdentityLabel,
    Scrobble,
    Source,
    SourceKind,
    UnsourcedIdentityError,
)
from pipeline.serde import artist_from_dict, artist_to_dict
from recommender.feedback import Feedback


@pytest.fixture
def mem_cache():
    cache = Cache(":memory:")
    yield cache
    cache.close()


def test_artist_round_trips_through_cache(mem_cache, catalog) -> None:
    artist = catalog["mitski"]
    mem_cache.put_artist(artist, fetched_at="2026-05-31")
    loaded = mem_cache.get_artist("mitski")
    assert loaded == artist
    assert mem_cache.artist_fetched_at("mitski") == "2026-05-31"  # lineage preserved


def test_missing_artist_returns_none(mem_cache) -> None:
    assert mem_cache.get_artist("nope") is None
    assert mem_cache.artist_fetched_at("nope") is None


def test_scrobbles_round_trip_ordered(mem_cache) -> None:
    scrobbles = [
        Scrobble("a", "A", "t2", 200),
        Scrobble("a", "A", "t1", 100),
    ]
    mem_cache.put_scrobbles("user", scrobbles)
    loaded = mem_cache.get_scrobbles("user")
    assert [s.ts for s in loaded] == [100, 200]  # ordered by ts


def test_last_synced_ts_is_zero_for_unknown_user(mem_cache) -> None:
    """No history yet -> the since-cursor for a full first sync (FIX-02)."""
    assert mem_cache.last_synced_ts("nobody") == 0


def test_last_synced_ts_tracks_the_newest_scrobble(mem_cache) -> None:
    mem_cache.put_scrobbles(
        "user",
        [
            Scrobble("a", "A", "t1", 100),
            Scrobble("a", "A", "t2", 300),
            Scrobble("a", "A", "t3", 200),
        ],
    )
    assert mem_cache.last_synced_ts("user") == 300
    # unrelated users don't share a watermark
    assert mem_cache.last_synced_ts("someone-else") == 0


def test_http_cache_roundtrip(mem_cache) -> None:
    assert mem_cache.get_cached_response("u://1") is None
    mem_cache.put_cached_response("u://1", "body", "2026-05-31")
    assert mem_cache.get_cached_response("u://1") == "body"


def test_cache_creates_missing_parent_dir_for_str_path(tmp_path) -> None:
    """A str db_path (e.g. from the CLI's --db argument) must create data/ too.

    Previously only a Path instance triggered the mkdir, so `lavender refresh` (which
    passes DEFAULT_DB_PATH's str form) crashed with sqlite3.OperationalError on a
    fresh clone with no `data/` directory yet.
    """
    db_path = str(tmp_path / "nested" / "cache.db")
    cache = Cache(db_path)
    try:
        cache.put_cached_response("u://1", "body", "2026-05-31")
        assert cache.get_cached_response("u://1") == "body"
    finally:
        cache.close()


def test_composition_round_trips(mem_cache, catalog) -> None:
    band = catalog["big-thief"]
    assert band.female_fronted is True
    mem_cache.put_artist(band, fetched_at="2026-05-31")
    loaded = mem_cache.get_artist("big-thief")
    assert loaded is not None and loaded.female_fronted is True


def test_corrupt_cache_row_violating_guardrail_raises_on_load() -> None:
    """A tampered row with a gender but no source must not load as a clean label."""
    payload = {
        "artist_id": "x",
        "name": "X",
        "tags": [],
        "identity": {
            "gender": "woman",
            "basis": "self-identified",
            "sources": [],
            "confidence": None,
        },
        "composition": None,
        "listeners": 0,
        "playcount": 0,
    }
    with pytest.raises(UnsourcedIdentityError):
        artist_from_dict(payload)


def test_feedback_round_trips(mem_cache) -> None:
    fb = Feedback(username="chelsea", artist_id="mitski", vote=1, ts=100)
    mem_cache.record_feedback(fb, fetched_at="2026-07-02")
    loaded = mem_cache.load_feedback("chelsea")
    assert loaded == [fb]


def test_feedback_upserts_on_revote(mem_cache) -> None:
    """A re-vote on the same (username, artist_id) replaces, not duplicates."""
    mem_cache.record_feedback(
        Feedback(username="chelsea", artist_id="mitski", vote=1, ts=100), fetched_at="2026-07-01"
    )
    mem_cache.record_feedback(
        Feedback(username="chelsea", artist_id="mitski", vote=-1, ts=200), fetched_at="2026-07-02"
    )
    loaded = mem_cache.load_feedback("chelsea")
    assert loaded == [Feedback(username="chelsea", artist_id="mitski", vote=-1, ts=200)]


def test_feedback_is_scoped_per_user(mem_cache) -> None:
    mem_cache.record_feedback(
        Feedback(username="chelsea", artist_id="mitski", vote=1, ts=100), fetched_at="2026-07-02"
    )
    mem_cache.record_feedback(
        Feedback(username="other", artist_id="mitski", vote=-1, ts=100), fetched_at="2026-07-02"
    )
    assert mem_cache.load_feedback("chelsea") == [
        Feedback(username="chelsea", artist_id="mitski", vote=1, ts=100)
    ]
    assert mem_cache.load_feedback("nobody") == []


def test_fresh_cache_is_created_at_current_schema_version(mem_cache) -> None:
    assert mem_cache.schema_version == CACHE_SCHEMA_VERSION


def test_migration_from_v1_adds_feedback_table(tmp_path) -> None:
    """A cache written before the feedback migration gains the table in place."""
    db_path = tmp_path / "legacy.db"

    # Simulate a pre-migration (v1) cache: base schema only, no feedback table,
    # user_version left at 0 (as a real legacy file would be).
    import sqlite3

    from pipeline.cache import _BASE_SCHEMA

    conn = sqlite3.connect(str(db_path))
    conn.executescript(_BASE_SCHEMA)
    conn.commit()
    conn.close()

    with Cache(db_path) as cache:
        assert cache.schema_version == CACHE_SCHEMA_VERSION
        fb = Feedback(username="chelsea", artist_id="mitski", vote=1, ts=100)
        cache.record_feedback(fb, fetched_at="2026-07-02")
        assert cache.load_feedback("chelsea") == [fb]


def test_newer_cache_schema_raises(tmp_path) -> None:
    """A cache written by a newer version of the code must not be misread."""
    from pipeline.cache import CacheSchemaError

    db_path = tmp_path / "future.db"
    with Cache(db_path):
        pass  # create it at the current version, then bump it past what we support

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    with pytest.raises(CacheSchemaError):
        Cache(db_path)


def test_artist_to_dict_is_json_shaped() -> None:
    artist = Artist(
        "x",
        "X",
        identity=IdentityLabel(
            gender=Gender.WOMAN,
            basis=IdentityBasis.SELF_IDENTIFIED,
            sources=(Source(SourceKind.WIKIDATA_P21, "c", "2026-05-31", "Q1"),),
            confidence=0.8,
        ),
    )
    d = artist_to_dict(artist)
    assert d["identity"]["gender"] == "woman"
    assert d["identity"]["sources"][0]["kind"] == "wikidata-p21"
    assert artist_from_dict(d) == artist
