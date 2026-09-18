"""Independent synthetic-world builders for the eval (FIX-06: de-circularize).

``pipeline.demo``'s own docstring admits its world is "tuned so the hybrid
recommender recovers genuine held-out discoveries". Useful as a smoke test and
as the dashboard's demo mode — but as the *only* quantitative evidence in
``docs/audits/eval-report.json``, grading the recommender on a fixture built to
make it pass is circular.

This module factors the demo-world construction pattern (seeds/candidates/
scrobbles/similarity graph -> a temporal-split-able world) into a small,
declarative builder, and uses it to add four more worlds with genuinely
different structure:

* :func:`indie_tuned_world`       — (a) the existing, hand-tuned demo world.
* :func:`sparse_tags_world`       — (b) most artists carry empty/single tags;
  the content signal is weak almost everywhere.
* :func:`popularity_skewed_world` — (c) a couple of mega-listener artists with
  zero relevance dominate the popularity baseline.
* :func:`no_collaborative_world`  — (d) the similarity graph is empty, so only
  the content (tag-cosine) signal can fire.
* :func:`adversarial_near_miss_world` — (e) popular decoys share a tag with the
  train profile (content near-misses) without the similarity edges the true
  discoveries have — designed to fool a naive tag-only ranker.

Each world's candidate pool is deliberately larger than the eval's ``k`` (#82).
It used to be exactly four candidates against ``k=5``, which made the top-k the
entire pool: ``recall_at_k`` came out 1.0 in four of five worlds for *every*
model, including a random or reversed ranking, and the aggregate then averaged
that structurally-pinned number in with the one world that could discriminate.
``recommender.eval`` now reports which worlds could not discriminate and
excludes them from the mean; this is the other half — a fixture where the metric
is a tautology is not a test of the ranker.
``tests/test_eval_worlds.py`` asserts the pools stay bigger than ``k``.

Every builder returns ``(username, scrobbles, catalog, source)`` — the same
shape ``pipeline.demo`` already yields to ``recommender.eval.evaluate`` — with
a nonempty temporal test split and at least one genuine ground-truth positive.
None of these four are tuned to make the hybrid win; only :func:`indie_tuned_world`
carries that caveat, and it is labeled as such in ``ALL_WORLDS``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pipeline.demo import DEMO_USER, demo_catalog, demo_scrobbles, demo_source
from pipeline.enrich import FixtureEnricher
from pipeline.ingest import enrich_artist
from pipeline.lastfm import FixtureLastfm
from pipeline.models import Artist, Scrobble

#: ``(username, scrobbles, catalog, source)`` — what every builder returns.
World = tuple[str, list[Scrobble], dict[str, Artist], FixtureLastfm]

_EPOCH = 1_700_000_000  # arbitrary but fixed unix-seconds start, for determinism


@dataclass(frozen=True)
class _ArtistSpec:
    artist_id: str
    name: str
    tags: tuple[str, ...] = ()
    listeners: int = 0


@dataclass(frozen=True)
class _WorldSpec:
    """Declarative shape for one synthetic world; :func:`_build` renders it."""

    username: str
    seeds: tuple[_ArtistSpec, ...]  # artists heard in the train window
    candidates: tuple[_ArtistSpec, ...]  # the rest of the recommendable pool
    similar: dict[str, list[tuple[str, float]]]  # collaborative edges
    train_plan: dict[str, int]  # artist_id -> play count, train window
    discoveries: tuple[str, ...]  # candidate ids first heard in the test window


def _artists_by_id(spec: _WorldSpec) -> dict[str, _ArtistSpec]:
    return {a.artist_id: a for a in (*spec.seeds, *spec.candidates)}


def _scrobbles_for(spec: _WorldSpec) -> list[Scrobble]:
    """Earlier plays of the train plan; later 'discovery' plays of held-outs."""
    by_id = _artists_by_id(spec)
    events: list[Scrobble] = []
    ts = _EPOCH
    for artist_id, count in spec.train_plan.items():
        name = by_id[artist_id].name
        for _ in range(count):
            events.append(Scrobble(artist_id, name, f"{name} track", ts))
            ts += 3600
    for artist_id in spec.discoveries:
        name = by_id[artist_id].name
        for _ in range(2):
            events.append(Scrobble(artist_id, name, f"{name} track", ts))
            ts += 3600
    return events


def _build(spec: _WorldSpec) -> World:
    all_artists = (*spec.seeds, *spec.candidates)
    tags = {a.artist_id: a.tags for a in all_artists}
    scrobbles = _scrobbles_for(spec)
    source = FixtureLastfm(
        scrobbles={spec.username: scrobbles}, tags=tags, similar=dict(spec.similar)
    )
    enricher = FixtureEnricher(gender={}, composition={})  # identity is irrelevant to eval ranking
    catalog: dict[str, Artist] = {}
    for a in all_artists:
        enriched = enrich_artist(a.artist_id, a.name, source, enricher, listeners=a.listeners)
        catalog[a.artist_id] = Artist(
            artist_id=enriched.artist_id,
            name=enriched.name,
            tags=a.tags,
            identity=enriched.identity,
            composition=enriched.composition,
            listeners=a.listeners,
            playcount=enriched.playcount,
        )
    return spec.username, scrobbles, catalog, source


# --- (a) the existing, hand-tuned demo world --------------------------------


def indie_tuned_world() -> World:
    """The world ``pipeline.demo``/the dashboard already use.

    Kept as one voice among several precisely because it is hand-tuned (see
    module docstring) — grading against it *alone* is the circularity FIX-06
    fixes. Its tuning is disclosed in ``ALL_WORLDS`` and in every aggregated
    report's ``caveats`` field.
    """
    return DEMO_USER, demo_scrobbles(), demo_catalog(), demo_source()


# --- (b) sparse tags: most artists carry empty/single tag metadata ---------


def sparse_tags_world() -> World:
    """Most artists have empty or single-tag metadata.

    The content (tag-cosine) signal is weak-to-absent for most candidates, so
    a hybrid that leans entirely on tags should *not* clearly win here — this
    checks the collaborative signal alone can still beat popularity when the
    listened seeds are genuinely similar to the held-out discoveries.
    """
    seeds = (
        _ArtistSpec("sp-nova", "Nova Static", (), 100_000),
        _ArtistSpec("sp-halo", "Halo Drift", ("lofi",), 80_000),
        _ArtistSpec("sp-drift", "Drift Season", (), 60_000),
    )
    candidates = (
        _ArtistSpec("sp-echo", "Echo Bloom", (), 40_000),  # discovery, no tags at all
        _ArtistSpec("sp-glow", "Glow Static", ("lofi",), 30_000),  # discovery, one tag
        _ArtistSpec("sp-noise", "Noise Machine", (), 900_000),  # decoy: popular, no signal
        _ArtistSpec("sp-static", "Static Empire", (), 500_000),  # decoy: popular, no signal
        # #82: four more decoys so the rankable pool (8) exceeds the eval's k.
        # Deliberately mixed in popularity so widening the pool does not itself
        # tilt the comparison: two rank above the discoveries on the popularity
        # baseline and two below, and none carries a tag or similarity edge.
        _ArtistSpec("sp-quarry", "Quarry Lights", (), 700_000),  # decoy: popular
        _ArtistSpec("sp-ember", "Ember Static", ("lofi",), 450_000),  # decoy: tag, no edge
        _ArtistSpec("sp-cinder", "Cinder Vale", (), 20_000),  # decoy: obscure
        _ArtistSpec("sp-marl", "Marl Field", (), 5_000),  # decoy: obscure
    )
    similar = {
        "sp-nova": [("sp-echo", 0.90), ("sp-glow", 0.45)],
        "sp-halo": [("sp-glow", 0.80)],
        "sp-drift": [("sp-echo", 0.55)],
    }
    spec = _WorldSpec(
        username="sparse-listener",
        seeds=seeds,
        candidates=candidates,
        similar=similar,
        train_plan={"sp-nova": 8, "sp-halo": 6, "sp-drift": 5},
        discoveries=("sp-echo", "sp-glow"),
    )
    return _build(spec)


# --- (c) popularity-skewed: a couple of irrelevant mega-listener artists ---


def popularity_skewed_world() -> World:
    """A couple of mega-listener artists dominate raw popularity but share no
    tag or similarity signal with anything the listener actually plays.

    The popularity baseline ranks them first; the true discoveries are far
    less popular but genuinely connected (tags + similarity). This is the
    clearest test of whether the hybrid is doing anything beyond "loud wins".
    """
    seeds = (
        _ArtistSpec("ps-vale", "Vale Signal", ("synth pop", "dream"), 200_000),
        _ArtistSpec("ps-orbit", "Orbit June", ("synth pop",), 150_000),
        _ArtistSpec("ps-fern", "Fern Radio", ("dream", "ambient"), 100_000),
    )
    candidates = (
        _ArtistSpec("ps-comet", "Comet Season", ("synth pop", "dream"), 90_000),  # discovery
        _ArtistSpec("ps-luna", "Luna Arcade", ("ambient",), 70_000),  # discovery
        _ArtistSpec("ps-titan", "Titan Arena", ("classic rock",), 30_000_000),  # decoy, irrelevant
        _ArtistSpec("ps-giant", "Giant Static", (), 25_000_000),  # decoy, irrelevant
        # #82: pool widened past k. Mixed popularity again, and one decoy that
        # *is* tag-adjacent, so the extra candidates are not uniformly easy.
        _ArtistSpec("ps-colossus", "Colossus Fair", ("classic rock",), 18_000_000),
        _ArtistSpec("ps-murmur", "Murmur Lane", ("dream",), 60_000),  # tag-adjacent, no edge
        _ArtistSpec("ps-slate", "Slate Harbour", (), 40_000),  # obscure, no signal
        _ArtistSpec("ps-pier", "Pier Nine", (), 12_000),  # obscure, no signal
    )
    similar = {
        "ps-vale": [("ps-comet", 0.85)],
        "ps-orbit": [("ps-comet", 0.50)],
        "ps-fern": [("ps-luna", 0.80)],
    }
    spec = _WorldSpec(
        username="skew-listener",
        seeds=seeds,
        candidates=candidates,
        similar=similar,
        train_plan={"ps-vale": 8, "ps-orbit": 6, "ps-fern": 5},
        discoveries=("ps-comet", "ps-luna"),
    )
    return _build(spec)


# --- (d) no collaborative signal: the similarity graph is empty ------------


def no_collaborative_world() -> World:
    """The similarity graph is entirely empty — only the content signal fires.

    Checks the hybrid degrades gracefully to tag-cosine ranking (rather than,
    say, collapsing to an all-zero score) and still beats popularity.
    """
    seeds = (
        _ArtistSpec("nc-solstice", "Solstice Bell", ("folk", "acoustic"), 300_000),
        _ArtistSpec("nc-ember", "Ember Row", ("folk",), 250_000),
        _ArtistSpec("nc-birch", "Birch Hollow", ("acoustic", "singer-songwriter"), 200_000),
    )
    candidates = (
        _ArtistSpec("nc-cedar", "Cedar Lane", ("folk", "acoustic"), 120_000),  # discovery
        _ArtistSpec("nc-willow", "Willow Verse", ("singer-songwriter",), 90_000),  # discovery
        _ArtistSpec("nc-anthem", "Anthem Fields", ("stadium rock",), 4_000_000),  # decoy
        _ArtistSpec("nc-brass", "Brass Parade", (), 1_000_000),  # decoy
        # #82: pool widened past k. With no similarity graph anywhere in this
        # world, a tag-adjacent decoy is the hardest kind of candidate the
        # content signal can face, so one is included rather than only easy ones.
        _ArtistSpec("nc-thistle", "Thistle Court", ("folk",), 2_500_000),  # tag-adjacent decoy
        _ArtistSpec("nc-quay", "Quay Street", ("stadium rock",), 800_000),
        _ArtistSpec("nc-lantern", "Lantern Bay", (), 150_000),
        _ArtistSpec("nc-reed", "Reed Hollow", (), 30_000),
    )
    spec = _WorldSpec(
        username="no-collab-listener",
        seeds=seeds,
        candidates=candidates,
        similar={},  # deliberately empty: no collaborative edges anywhere
        train_plan={"nc-solstice": 8, "nc-ember": 6, "nc-birch": 5},
        discoveries=("nc-cedar", "nc-willow"),
    )
    return _build(spec)


# --- (e) adversarial near-misses --------------------------------------------


def adversarial_near_miss_world() -> World:
    """Popular decoys share exactly one tag with the profile (a "near miss")
    but carry none of the similarity-graph evidence the true discoveries do.

    Designed to fool a ranker that leans on content alone: the decoys are both
    tag-adjacent *and* far more popular than the genuine discoveries.
    """
    seeds = (
        _ArtistSpec("am-nova", "Nova Vale", ("shoegaze", "dream pop"), 180_000),
        _ArtistSpec("am-tide", "Tide Low", ("shoegaze",), 150_000),
        _ArtistSpec("am-glass", "Glass Fern", ("dream pop", "ambient"), 140_000),
    )
    candidates = (
        _ArtistSpec("am-echo", "Echo Vale", ("shoegaze", "dream pop"), 60_000),  # discovery
        _ArtistSpec("am-mist", "Mist Hollow", ("ambient",), 50_000),  # discovery
        # Near-miss decoys: one shared tag each, huge popularity, no similarity edge.
        _ArtistSpec("am-decoy-one", "Decoy Static", ("shoegaze",), 2_000_000),
        _ArtistSpec("am-decoy-two", "Decoy Arena", ("dream pop",), 1_500_000),
        # #82: pool widened past k, staying in this world's premise — every
        # added decoy is another near miss, which makes the world harder rather
        # than easier for the hybrid.
        _ArtistSpec("am-decoy-three", "Decoy Chapel", ("shoegaze",), 1_100_000),
        _ArtistSpec("am-decoy-four", "Decoy Harbour", ("dream pop",), 900_000),
        _ArtistSpec("am-decoy-five", "Decoy Meadow", ("ambient",), 400_000),
        _ArtistSpec("am-decoy-six", "Decoy Signal", ("shoegaze", "ambient"), 250_000),
    )
    similar = {
        "am-nova": [("am-echo", 0.90)],
        "am-tide": [("am-echo", 0.60)],
        "am-glass": [("am-mist", 0.85)],
    }
    spec = _WorldSpec(
        username="adversarial-listener",
        seeds=seeds,
        candidates=candidates,
        similar=similar,
        train_plan={"am-nova": 8, "am-tide": 6, "am-glass": 5},
        discoveries=("am-echo", "am-mist"),
    )
    return _build(spec)


#: Every fixture family the eval runs across (FIX-06). >=4 structurally
#: independent worlds, plus the hand-tuned demo world disclosed as such.
ALL_WORLDS: dict[str, Callable[[], World]] = {
    "demo-tuned-indie": indie_tuned_world,
    "sparse-tags": sparse_tags_world,
    "popularity-skewed": popularity_skewed_world,
    "no-collaborative-signal": no_collaborative_world,
    "adversarial-near-misses": adversarial_near_miss_world,
}
