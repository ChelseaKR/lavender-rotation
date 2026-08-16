"""FIX-10: the local corrections ledger — cited, priority-winning, refresh-safe."""

from __future__ import annotations

import pytest
from pipeline.cache import Cache
from pipeline.enrich import FixtureEnricher
from pipeline.identity import IdentityEvidence
from pipeline.ingest import enrich_artist
from pipeline.lastfm import FixtureLastfm
from pipeline.models import Gender, SourceKind, UnsourcedIdentityError


@pytest.fixture
def mem_cache():
    cache = Cache(":memory:")
    yield cache
    cache.close()


def test_put_correction_requires_citation(mem_cache) -> None:
    """No citation, no override — the same invariant as any other Source."""
    uncited = IdentityEvidence(
        kind=SourceKind.ARTIST_STATEMENT,
        value="woman",
        citation="   ",
        retrieved_at="2026-07-01",
    )
    with pytest.raises(UnsourcedIdentityError):
        mem_cache.put_correction("mystery-act", uncited, entered_at="2026-07-01")
    assert mem_cache.get_corrections("mystery-act") == ()


def test_put_correction_requires_artist_statement_kind(mem_cache) -> None:
    """A correction must be recorded as ARTIST_STATEMENT, not any permitted kind."""
    wrong_kind = IdentityEvidence(
        kind=SourceKind.WIKIDATA_P21,
        value="Q6581072",
        citation="https://www.wikidata.org/wiki/Q1",
        retrieved_at="2026-07-01",
    )
    with pytest.raises(UnsourcedIdentityError):
        mem_cache.put_correction("mystery-act", wrong_kind, entered_at="2026-07-01")


def test_put_and_get_corrections_round_trip(mem_cache) -> None:
    evidence = IdentityEvidence(
        kind=SourceKind.ARTIST_STATEMENT,
        value="nonbinary",
        citation="https://example.org/correction-x",
        retrieved_at="2026-07-01",
    )
    mem_cache.put_correction("mystery-act", evidence, entered_at="2026-07-02")
    corrections = mem_cache.get_corrections("mystery-act")
    assert len(corrections) == 1
    got = corrections[0]
    assert got.kind is SourceKind.ARTIST_STATEMENT
    assert got.value == "nonbinary"
    assert got.citation == "https://example.org/correction-x"
    assert got.is_local_correction is True
    # A different artist sees nothing.
    assert mem_cache.get_corrections("some-other-artist") == ()


def test_list_corrections_covers_every_artist(mem_cache) -> None:
    ev_a = IdentityEvidence(SourceKind.ARTIST_STATEMENT, "woman", "https://a", "2026-07-01")
    ev_b = IdentityEvidence(SourceKind.ARTIST_STATEMENT, "man", "https://b", "2026-07-01")
    mem_cache.put_correction("artist-a", ev_a, entered_at="2026-07-01")
    mem_cache.put_correction("artist-b", ev_b, entered_at="2026-07-02")
    all_corrections = mem_cache.list_corrections()
    assert [artist_id for artist_id, _ev, _entered in all_corrections] == ["artist-a", "artist-b"]


def _enricher_asserting_man() -> FixtureEnricher:
    return FixtureEnricher(
        gender={
            "mystery-act": [
                IdentityEvidence(
                    kind=SourceKind.WIKIDATA_P21,
                    value="Q6581097",  # male
                    citation="https://www.wikidata.org/wiki/Qx",
                    retrieved_at="2026-05-31",
                )
            ]
        },
        composition={},
    )


def test_correction_applies_at_resolve_time_and_wins_by_priority(mem_cache) -> None:
    """A correction is an ARTIST_STATEMENT (priority 3) — it outranks WIKIDATA_P21."""
    source = FixtureLastfm(scrobbles={}, tags={"mystery-act": ()}, similar={})
    enricher = _enricher_asserting_man()

    # Before any correction: the enricher's WIKIDATA_P21 evidence wins alone.
    before = enrich_artist("mystery-act", "Mystery Act", source, enricher, cache=mem_cache)
    assert before.identity.gender is Gender.MAN

    correction = IdentityEvidence(
        kind=SourceKind.ARTIST_STATEMENT,
        value="nonbinary",
        citation="https://example.org/mystery-act-correction",
        retrieved_at="2026-07-01",
    )
    mem_cache.put_correction("mystery-act", correction, entered_at="2026-07-01")

    after = enrich_artist("mystery-act", "Mystery Act", source, enricher, cache=mem_cache)
    assert after.identity.gender is Gender.NONBINARY
    assert after.identity.conflict is True  # WIKIDATA_P21 (male) still disagrees
    correction_sources = [s for s in after.identity.sources if s.is_local_correction]
    assert correction_sources, "the winning source should be flagged as a local correction"
    assert correction_sources[0].citation == "https://example.org/mystery-act-correction"


def test_no_cache_means_no_corrections_applied() -> None:
    """Without a cache, enrich_artist behaves exactly as before FIX-10."""
    source = FixtureLastfm(scrobbles={}, tags={"mystery-act": ()}, similar={})
    enricher = _enricher_asserting_man()
    artist = enrich_artist("mystery-act", "Mystery Act", source, enricher, cache=None)
    assert artist.identity.gender is Gender.MAN
    assert artist.identity.conflict is False


def test_cache_schema_migration_is_idempotent_on_reopen(tmp_path) -> None:
    """Reopening an already-migrated cache is a no-op (the version guard skips it)."""
    db_path = tmp_path / "cache.db"
    with Cache(db_path) as cache:
        ev = IdentityEvidence(SourceKind.ARTIST_STATEMENT, "woman", "https://x", "2026-07-01")
        cache.put_correction("artist-a", ev, entered_at="2026-07-01")

    # Reopen — the corrections table + row must still be there, untouched.
    with Cache(db_path) as cache:
        corrections = cache.get_corrections("artist-a")
        assert len(corrections) == 1
        assert corrections[0].citation == "https://x"


def test_corrections_survive_refresh(mem_cache) -> None:
    """`lavender refresh` only expires http_cache — corrections are untouched."""
    correction = IdentityEvidence(
        kind=SourceKind.ARTIST_STATEMENT,
        value="woman",
        citation="https://example.org/survives",
        retrieved_at="2026-07-01",
    )
    mem_cache.put_correction("mystery-act", correction, entered_at="2026-07-01")
    mem_cache.put_cached_response("https://example.org/some-api-call", "{}", "2026-07-01")

    cleared = mem_cache.expire_http_cache(ttl_days=0, now="2026-07-02")

    assert cleared >= 1
    assert mem_cache.get_cached_response("https://example.org/some-api-call") is None
    survivors = mem_cache.get_corrections("mystery-act")
    assert len(survivors) == 1
    assert survivors[0].citation == "https://example.org/survives"
