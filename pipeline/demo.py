"""A small, fully-offline demo world used by demo mode, the eval, and tests.

It deliberately spans every identity basis the system must handle responsibly:

* sourced **women** (MusicBrainz gender, Wikidata P21, artist statement),
* a sourced **nonbinary** solo artist (artist statement),
* sourced **female-fronted** bands (Discogs lineup, distinct from member gender),
* sourced **men** (present on musical merit, neither boosted nor penalised), and
* a first-class **unknown** artist (surfaced on similarity alone).

The listening history and similarity graph are tuned so the hybrid recommender
recovers genuine held-out discoveries that a popularity baseline misses.
"""

from __future__ import annotations

from pipeline.enrich import FixtureEnricher
from pipeline.identity import IdentityEvidence, resolve_identity
from pipeline.ingest import build_profile
from pipeline.lastfm import FixtureLastfm
from pipeline.models import Artist, FrontPerson, ListeningProfile, Scrobble, SourceKind

DEMO_USER = "demo"
_FETCHED = "2026-05-31"

# (artist_id, display name, tags, listeners) ---------------------------------
_SEEDS = [
    ("mitski", "Mitski", ("indie rock", "art pop"), 1_200_000),
    ("big-thief", "Big Thief", ("indie folk", "indie rock"), 900_000),
    ("phoebe-bridgers", "Phoebe Bridgers", ("indie folk", "sad"), 1_500_000),
    ("japanese-breakfast", "Japanese Breakfast", ("indie pop", "dream pop"), 800_000),
]
_CANDIDATES = [
    ("soccer-mommy", "Soccer Mommy", ("indie rock", "bedroom pop"), 300_000),
    ("snail-mail", "Snail Mail", ("indie rock",), 200_000),
    ("lucy-dacus", "Lucy Dacus", ("indie folk", "indie rock"), 350_000),
    ("boygenius", "boygenius", ("indie rock", "supergroup"), 600_000),
    ("adrianne-lenker", "Adrianne Lenker", ("indie folk",), 250_000),
    ("mystery-act", "Mystery Act", ("indie rock",), 50_000),  # UNKNOWN identity
    ("big-pop-dude", "Big Pop Dude", ("pop", "mainstream"), 5_000_000),
    ("arena-men", "Arena Men", ("arena rock",), 4_000_000),  # UNKNOWN identity, popular
    ("moses-sumney", "Moses Sumney", ("art pop",), 800_000),  # sourced man
    ("shamir", "Shamir", ("art pop", "experimental"), 400_000),  # sourced nonbinary
]

# Similar-artist edges from each listened seed (the collaborative signal).
_SIMILAR = {
    "mitski": [("soccer-mommy", 0.90), ("snail-mail", 0.85), ("mystery-act", 0.60)],
    "big-thief": [("adrianne-lenker", 0.90), ("lucy-dacus", 0.70)],
    "phoebe-bridgers": [("lucy-dacus", 0.90), ("boygenius", 0.80), ("soccer-mommy", 0.50)],
    "japanese-breakfast": [("shamir", 0.70), ("soccer-mommy", 0.40)],
}


def _ev(kind: SourceKind, value: str, citation: str) -> IdentityEvidence:
    return IdentityEvidence(kind=kind, value=value, citation=citation, retrieved_at=_FETCHED)


# Sourced individual-gender evidence (women / man / nonbinary). -----------------
_GENDER_EVIDENCE: dict[str, list[IdentityEvidence]] = {
    "mitski": [
        _ev(
            SourceKind.MUSICBRAINZ_GENDER,
            "female",
            "https://musicbrainz.org/artist/fa58cf24-0e44-421d-8519-8bf461dcfaa5",
        ),
        _ev(SourceKind.WIKIDATA_P21, "Q6581072", "https://www.wikidata.org/wiki/Q16735549"),
    ],
    "phoebe-bridgers": [
        _ev(SourceKind.WIKIDATA_P21, "Q6581072", "https://www.wikidata.org/wiki/Q28907802"),
    ],
    "japanese-breakfast": [
        _ev(SourceKind.ARTIST_STATEMENT, "woman", "https://example.org/zauner-interview"),
    ],
    "soccer-mommy": [
        _ev(
            SourceKind.MUSICBRAINZ_GENDER,
            "female",
            "https://musicbrainz.org/artist/84803b1d-dcb3-490d-9af5-debbba64a011",
        ),
    ],
    "snail-mail": [
        _ev(
            SourceKind.MUSICBRAINZ_GENDER,
            "female",
            "https://musicbrainz.org/artist/86cd4d38-857c-42bd-a5da-9acedcab1e01",
        ),
    ],
    "lucy-dacus": [
        _ev(SourceKind.WIKIDATA_P21, "Q6581072", "https://www.wikidata.org/wiki/Q47545178"),
    ],
    "adrianne-lenker": [
        _ev(SourceKind.ARTIST_STATEMENT, "woman", "https://example.org/lenker"),
    ],
    "moses-sumney": [
        _ev(
            SourceKind.MUSICBRAINZ_GENDER,
            "male",
            "https://musicbrainz.org/artist/89c081d4-2ab2-4d3e-8589-ad77dfc40384",
        ),
    ],
    "shamir": [
        _ev(SourceKind.ARTIST_STATEMENT, "nonbinary", "https://example.org/shamir-nb"),
    ],
    # mystery-act, arena-men, big-pop-dude... see below; the first two stay unknown.
    # An invented act, so it cannot carry a real MusicBrainz record. It cites an
    # example.org statement like the demo's other illustrative citations rather
    # than a fabricated MBID: a made-up identifier in a real registry's URL space
    # is the one citation shape this project must not model.
    "big-pop-dude": [
        _ev(SourceKind.ARTIST_STATEMENT, "man", "https://example.org/big-pop-dude-statement"),
    ],
}


# Sourced band-composition (female-fronted), distinct from any member's gender. -
def _front(name: str, statement: str, citation: str) -> FrontPerson:
    return FrontPerson(
        name=name,
        role="lead vocals",
        identity=resolve_identity([_ev(SourceKind.ARTIST_STATEMENT, statement, citation)]),
    )


_COMPOSITION: dict[str, tuple[list[FrontPerson], list[IdentityEvidence]]] = {
    "big-thief": (
        [_front("Adrianne Lenker", "woman", "https://example.org/lenker")],
        [_ev(SourceKind.DISCOGS_LINEUP, "lineup", "https://www.discogs.com/artist/big-thief")],
    ),
    "boygenius": (
        [
            _front("Julien Baker", "woman", "https://example.org/jbaker"),
            _front("Lucy Dacus", "woman", "https://example.org/ldacus"),
        ],
        [_ev(SourceKind.DISCOGS_LINEUP, "lineup", "https://www.discogs.com/artist/boygenius")],
    ),
}


def _scrobbles() -> list[Scrobble]:
    """Earlier plays of the four seeds; later 'discovery' plays of three women."""
    events: list[Scrobble] = []
    ts = 1_700_000_000
    # Train window: repeated plays of the seeds (counts shape collaborative weight).
    train_plan = {"mitski": 10, "phoebe-bridgers": 9, "big-thief": 8, "japanese-breakfast": 6}
    for aid, count in train_plan.items():
        name = _name_of(aid)
        for _ in range(count):
            events.append(Scrobble(aid, name, f"{name} track", ts))
            ts += 3600
    # Test window: genuine later discoveries the recommender should recover.
    for aid in ("lucy-dacus", "soccer-mommy", "adrianne-lenker"):
        for _ in range(2):
            events.append(Scrobble(aid, _name_of(aid), f"{_name_of(aid)} track", ts))
            ts += 3600
    return events


def _name_of(artist_id: str) -> str:
    for aid, name, _tags, _l in _SEEDS + _CANDIDATES:
        if aid == artist_id:
            return name
    return artist_id


def demo_source() -> FixtureLastfm:
    tags = {aid: t for aid, _n, t, _l in _SEEDS + _CANDIDATES}
    return FixtureLastfm(scrobbles={DEMO_USER: _scrobbles()}, tags=tags, similar=dict(_SIMILAR))


def demo_enricher() -> FixtureEnricher:
    return FixtureEnricher(gender=dict(_GENDER_EVIDENCE), composition=dict(_COMPOSITION))


def demo_scrobbles() -> list[Scrobble]:
    return _scrobbles()


def demo_catalog() -> dict[str, Artist]:
    """Enrich *every* artist (seeds + candidates) into a catalog with popularity."""
    source = demo_source()
    enricher = demo_enricher()
    catalog: dict[str, Artist] = {}
    for aid, name, tags, listeners in _SEEDS + _CANDIDATES:
        from pipeline.ingest import enrich_artist

        artist = enrich_artist(aid, name, source, enricher, listeners=listeners)
        # enrich_artist reads tags from the source, but ensure listeners are set.
        catalog[aid] = Artist(
            artist_id=artist.artist_id,
            name=artist.name,
            tags=tags,
            identity=artist.identity,
            composition=artist.composition,
            listeners=listeners,
            playcount=artist.playcount,
        )
    return catalog


def demo_profile() -> ListeningProfile:
    profile = build_profile(DEMO_USER, _scrobbles())
    catalog = demo_catalog()
    return ListeningProfile(
        username=profile.username,
        play_counts=profile.play_counts,
        artist_names=profile.artist_names,
        tags={aid: catalog[aid].tags for aid in profile.play_counts if aid in catalog},
    )
