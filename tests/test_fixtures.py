"""FIX-06: independent fixture families, not just the hand-tuned demo world."""

from __future__ import annotations

from pipeline.fixtures import ALL_WORLDS
from recommender.eval import ground_truth, temporal_split


def test_all_worlds_has_at_least_four_families() -> None:
    assert len(ALL_WORLDS) >= 4


def test_every_world_has_nonempty_split_and_real_positives() -> None:
    for name, build in ALL_WORLDS.items():
        username, scrobbles, catalog, source = build()
        assert username, f"{name}: empty username"
        assert scrobbles, f"{name}: empty scrobbles"
        assert catalog, f"{name}: empty catalog"
        assert source is not None

        train, test = temporal_split(scrobbles, 0.7)
        assert train, f"{name}: empty train split"
        assert test, f"{name}: empty test split"

        positives = ground_truth(train, test)
        assert positives, f"{name}: no ground-truth positives"
        assert positives <= set(catalog), f"{name}: positives missing from the catalog"


def test_worlds_are_structurally_independent_populations() -> None:
    """No two worlds should share a username or overlap in artist ids — each
    is a genuinely separate synthetic population, not a relabeled copy.
    """
    usernames: set[str] = set()
    artist_id_sets: list[frozenset[str]] = []
    for build in ALL_WORLDS.values():
        username, _scrobbles, catalog, _source = build()
        usernames.add(username)
        artist_id_sets.append(frozenset(catalog))

    assert len(usernames) == len(ALL_WORLDS)
    for i, ids_a in enumerate(artist_id_sets):
        for ids_b in artist_id_sets[i + 1 :]:
            assert not (ids_a & ids_b)


def test_demo_tuned_world_is_labeled_as_such() -> None:
    """The one hand-tuned world must be present and named honestly, so the
    caveat text (which names it) stays accurate.
    """
    assert "demo-tuned-indie" in ALL_WORLDS


def test_no_collaborative_world_really_has_no_similarity_edges() -> None:
    from pipeline.fixtures import no_collaborative_world

    _username, _scrobbles, catalog, source = no_collaborative_world()
    for artist_id in catalog:
        assert source.similar_artists(artist_id) == []
