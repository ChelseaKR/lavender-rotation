"""Live-enrichment gate (FIX-01) — the live path, proved offline.

The live enricher is the first code in this repo that turns an *upstream* answer
into an identity label, so it is where the guardrails are easiest to lose. Every
test here runs against recorded payloads through an injected fetcher: no socket
is opened (the suite's autouse guard would fail if one were), and the whole
parse → resolve → label chain is exercised.

Three properties get the most attention, because they are the ones that would
fail quietly:

* **Ambiguity resolves to unknown, not to a best guess.** Last.fm hands us bare
  names for a large share of a real history, and a name is not a person. A
  lookup that cannot identify exactly one record yields no evidence.
* **An upstream failure is unknown, not a crash and not a retry-until-something.**
* **A sourced lineup never becomes a claim about the band.** Members carry their
  own labels; the act's own identity stays ``UNKNOWN``.

Artist names and MBIDs here are invented. Recording a real musician's gender in
a test fixture would be exactly the unverified, redistributable identity claim
``docs/audits/identity-data-ethics.md`` forbids — the shipped demo world has a
verified-subject ledger for that reason (``tests/test_demo_citations.py``), and
synthetic payloads need no such ledger.
"""

from __future__ import annotations

import json

import pytest
from pipeline.cache import Cache
from pipeline.enrich import (
    MusicBrainzEnricher,
    is_fronting_role,
    musicbrainz_lookup_url,
    musicbrainz_search_url,
    parse_musicbrainz_fronting,
    parse_musicbrainz_search,
    parse_wikidata_entity,
    parse_wikidata_link,
    wikidata_entity_data_url,
)
from pipeline.http import CachedHttpFetcher, HttpFetchError, build_user_agent
from pipeline.identity import citation_problem, resolve_identity
from pipeline.ingest import (
    Candidate,
    catalog_from_cache,
    discover_candidates,
    enrich_candidates,
    ingest,
    profile_from_cache,
)
from pipeline.lastfm import (
    FixtureLastfm,
    SimilarArtist,
    artist_query,
    looks_like_mbid,
    parse_similar,
    parse_similar_named,
)
from pipeline.models import BAND_COMPOSITION_SOURCES, Artist, Gender, Scrobble

RETRIEVED = "2026-08-15"

SOLO_MBID = "11111111-1111-4111-8111-111111111111"
BAND_MBID = "22222222-2222-4222-8222-222222222222"
FRONT_MBID = "33333333-3333-4333-8333-333333333333"
DRUMMER_MBID = "44444444-4444-4444-8444-444444444444"
OTHER_MBID = "55555555-5555-4555-8555-555555555555"


def search_payload(*entries: dict[str, object]) -> str:
    return json.dumps({"count": len(entries), "artists": list(entries)})


def hit(mbid: str, name: str, score: int = 100) -> dict[str, object]:
    return {"id": mbid, "name": name, "score": score, "type": "Person"}


def artist_payload(
    mbid: str,
    name: str,
    *,
    gender: str | None = None,
    kind: str = "Person",
    qid: str | None = None,
    relations: list[dict[str, object]] | None = None,
) -> str:
    body: dict[str, object] = {"id": mbid, "name": name, "type": kind}
    if gender is not None:
        body["gender"] = gender
    rels = list(relations or [])
    if qid is not None:
        rels.append(
            {"type": "wikidata", "url": {"resource": f"https://www.wikidata.org/wiki/{qid}"}}
        )
    body["relations"] = rels
    return json.dumps(body)


def member_relation(mbid: str, name: str, roles: list[str]) -> dict[str, object]:
    return {
        "type": "member of band",
        "direction": "backward",
        "attributes": roles,
        "artist": {"id": mbid, "name": name, "type": "Person"},
    }


def wikidata_payload(qid: str, gender_qid: str) -> str:
    return json.dumps(
        {
            "entities": {
                qid: {
                    "id": qid,
                    "claims": {"P21": [{"mainsnak": {"datavalue": {"value": {"id": gender_qid}}}}]},
                }
            }
        }
    )


class FakeFetcher:
    """An injected :class:`pipeline.http.Fetcher` over recorded payloads."""

    def __init__(self, documents: dict[str, str], *, failing: set[str] | None = None) -> None:
        self.documents = documents
        self.failing = failing or set()
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if url in self.failing:
            raise HttpFetchError("identity-source request failed (HTTP 503)")
        try:
            return self.documents[url]
        except KeyError:
            raise HttpFetchError("identity-source request failed (HTTP 404)") from None


def enricher_for(documents: dict[str, str], **kw: object) -> MusicBrainzEnricher:
    fetcher = FakeFetcher(documents, **kw)  # type: ignore[arg-type]
    return MusicBrainzEnricher(fetcher, retrieved_at=RETRIEVED)


# --- entity resolution: a name is not a person ------------------------------


def test_an_mbid_artist_key_needs_no_search() -> None:
    """Last.fm gave us an id, so nothing is resolved by name at all."""
    fetcher = FakeFetcher({musicbrainz_lookup_url(SOLO_MBID): artist_payload(SOLO_MBID, "Solo")})
    enricher = MusicBrainzEnricher(fetcher, retrieved_at=RETRIEVED)

    assert enricher.resolve_mbid(SOLO_MBID) == SOLO_MBID
    assert fetcher.calls == []


def test_an_exact_unambiguous_name_resolves() -> None:
    found = parse_musicbrainz_search(
        json.loads(search_payload(hit(SOLO_MBID, "Quiet Cartography"))),
        "quiet cartography",
    )
    assert found == SOLO_MBID


def test_two_artists_of_the_same_name_resolve_to_nothing() -> None:
    """The whole point: a shared name is ambiguous, and ambiguous means unknown."""
    payload = json.loads(
        search_payload(hit(SOLO_MBID, "Common Name"), hit(OTHER_MBID, "Common Name"))
    )
    assert parse_musicbrainz_search(payload, "Common Name") is None


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        (hit(SOLO_MBID, "Nearly The Same"), "a different artist's name"),
        (hit(SOLO_MBID, "Exact Name", score=99), "an imperfect score"),
        ({"id": "not-a-uuid", "name": "Exact Name", "score": 100}, "an unusable id"),
        ({"name": "Exact Name", "score": "many"}, "an unparseable score"),
    ],
)
def test_a_weak_match_is_no_match(entry: dict[str, object], why: str) -> None:
    assert parse_musicbrainz_search(json.loads(search_payload(entry)), "Exact Name") is None, why


def test_a_search_that_answers_nothing_leaves_the_artist_unknown() -> None:
    documents = {musicbrainz_search_url("Ambiguous"): search_payload()}
    assert enricher_for(documents).gender_evidence("Ambiguous") == []


def test_malformed_search_payload_raises_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="must be an object"):
        parse_musicbrainz_search(["not", "an", "object"], "Anyone")


# --- sourced claims become cited evidence -----------------------------------


def test_musicbrainz_field_becomes_cited_evidence() -> None:
    documents = {
        musicbrainz_search_url("Quiet Cartography"): search_payload(
            hit(SOLO_MBID, "Quiet Cartography")
        ),
        musicbrainz_lookup_url(SOLO_MBID): artist_payload(
            SOLO_MBID, "Quiet Cartography", gender="female"
        ),
    }
    evidence = enricher_for(documents).gender_evidence("Quiet Cartography")

    assert [e.value for e in evidence] == ["female"]
    assert evidence[0].citation == f"https://musicbrainz.org/artist/{SOLO_MBID}"
    assert evidence[0].retrieved_at == RETRIEVED
    assert resolve_identity(evidence).gender is Gender.WOMAN


def test_a_linked_wikidata_claim_corroborates_the_musicbrainz_one() -> None:
    documents = {
        musicbrainz_lookup_url(SOLO_MBID): artist_payload(
            SOLO_MBID, "Solo", gender="female", qid="Q900001"
        ),
        wikidata_entity_data_url("Q900001"): wikidata_payload("Q900001", "Q6581072"),
    }
    label = resolve_identity(enricher_for(documents).gender_evidence(SOLO_MBID))

    assert label.gender is Gender.WOMAN
    assert len(label.sources) == 2
    assert not label.conflict
    assert label.confidence is not None and label.confidence > 0.8


def test_a_trans_womans_wikidata_claim_resolves_to_woman() -> None:
    """Q1052281 is 'trans woman'. It is a woman's self-identification, full stop."""
    documents = {
        musicbrainz_lookup_url(SOLO_MBID): artist_payload(SOLO_MBID, "Solo", qid="Q900002"),
        wikidata_entity_data_url("Q900002"): wikidata_payload("Q900002", "Q1052281"),
    }
    label = resolve_identity(enricher_for(documents).gender_evidence(SOLO_MBID))

    assert label.gender is Gender.WOMAN
    assert [source.detail for source in label.sources] == ["Q1052281"]


def test_disagreeing_upstream_sources_surface_as_a_conflict() -> None:
    documents = {
        musicbrainz_lookup_url(SOLO_MBID): artist_payload(
            SOLO_MBID, "Solo", gender="male", qid="Q900003"
        ),
        wikidata_entity_data_url("Q900003"): wikidata_payload("Q900003", "Q6581072"),
    }
    label = resolve_identity(enricher_for(documents).gender_evidence(SOLO_MBID))

    assert label.conflict
    assert len(label.conflicting_claims) == 2


@pytest.mark.parametrize("asserted", ["not applicable", "", "unknown", "she"])
def test_an_unmappable_upstream_value_stays_unknown(asserted: str) -> None:
    documents = {
        musicbrainz_lookup_url(SOLO_MBID): artist_payload(SOLO_MBID, "Solo", gender=asserted)
    }
    label = resolve_identity(enricher_for(documents).gender_evidence(SOLO_MBID))
    assert label.gender is Gender.UNKNOWN


def test_every_citation_the_live_enricher_emits_is_a_usable_locator() -> None:
    """A citation has to be an address the registry it names can resolve."""
    documents = {
        musicbrainz_lookup_url(SOLO_MBID): artist_payload(
            SOLO_MBID, "Solo", gender="female", qid="Q900004"
        ),
        wikidata_entity_data_url("Q900004"): wikidata_payload("Q900004", "Q6581072"),
    }
    for evidence in enricher_for(documents).gender_evidence(SOLO_MBID):
        assert citation_problem(evidence.kind, evidence.citation) is None, evidence


# --- upstream failure is unknown, never an exception ------------------------


def test_a_failed_fetch_resolves_to_unknown() -> None:
    url = musicbrainz_lookup_url(SOLO_MBID)
    enricher = enricher_for(
        {url: artist_payload(SOLO_MBID, "Solo", gender="female")}, failing={url}
    )

    assert enricher.gender_evidence(SOLO_MBID) == []
    assert enricher.composition_evidence(SOLO_MBID) == ([], [])


def test_a_malformed_payload_resolves_to_unknown() -> None:
    documents = {musicbrainz_lookup_url(SOLO_MBID): "<html>rate limited</html>"}
    assert enricher_for(documents).gender_evidence(SOLO_MBID) == []


def test_a_json_document_of_the_wrong_shape_resolves_to_unknown() -> None:
    documents = {musicbrainz_lookup_url(SOLO_MBID): "[1, 2, 3]"}
    assert enricher_for(documents).gender_evidence(SOLO_MBID) == []


def test_a_failing_wikidata_leg_keeps_the_musicbrainz_claim() -> None:
    """One source being down does not discard the other source's answer."""
    entity_url = wikidata_entity_data_url("Q900005")
    documents = {
        musicbrainz_lookup_url(SOLO_MBID): artist_payload(
            SOLO_MBID, "Solo", gender="female", qid="Q900005"
        ),
        entity_url: wikidata_payload("Q900005", "Q6581072"),
    }
    evidence = enricher_for(documents, failing={entity_url}).gender_evidence(SOLO_MBID)
    assert [e.value for e in evidence] == ["female"]


def test_an_artist_is_resolved_once_per_run() -> None:
    documents = {
        musicbrainz_search_url("Solo"): search_payload(hit(SOLO_MBID, "Solo")),
        musicbrainz_lookup_url(SOLO_MBID): artist_payload(SOLO_MBID, "Solo", gender="female"),
    }
    fetcher = FakeFetcher(documents)
    enricher = MusicBrainzEnricher(fetcher, retrieved_at=RETRIEVED)

    enricher.gender_evidence("Solo")
    enricher.composition_evidence("Solo")

    assert fetcher.calls.count(musicbrainz_search_url("Solo")) == 1


# --- lineup: sourced, separate, and never a claim about the act -------------


def test_a_groups_fronting_lineup_becomes_sourced_composition() -> None:
    documents = {
        musicbrainz_lookup_url(BAND_MBID): artist_payload(
            BAND_MBID,
            "The Band",
            kind="Group",
            relations=[
                member_relation(FRONT_MBID, "Lead Singer", ["lead vocals", "guitar"]),
                member_relation(DRUMMER_MBID, "Drummer", ["drums"]),
            ],
        ),
        musicbrainz_lookup_url(FRONT_MBID): artist_payload(
            FRONT_MBID, "Lead Singer", gender="female"
        ),
    }
    fronts, evidence = enricher_for(documents).composition_evidence(BAND_MBID)

    assert [f.name for f in fronts] == ["Lead Singer"]
    assert fronts[0].role == "lead vocals"
    assert fronts[0].identity.gender is Gender.WOMAN
    assert next(e.kind for e in evidence) in BAND_COMPOSITION_SOURCES


def test_a_sourced_lineup_is_never_a_claim_about_the_band_itself() -> None:
    """The guardrail: composition evidence establishes no *individual's* gender."""
    documents = {
        musicbrainz_lookup_url(BAND_MBID): artist_payload(
            BAND_MBID,
            "The Band",
            kind="Group",
            relations=[member_relation(FRONT_MBID, "Lead Singer", ["lead vocals"])],
        ),
        musicbrainz_lookup_url(FRONT_MBID): artist_payload(
            FRONT_MBID, "Lead Singer", gender="female"
        ),
    }
    enricher = enricher_for(documents)
    band = resolve_identity(enricher.gender_evidence(BAND_MBID))

    assert band.gender is Gender.UNKNOWN
    assert band.sources == ()


def test_a_nonbinary_fronted_band_is_not_called_female_fronted() -> None:
    """Surfaced by the lens, and described as the source described them."""
    from pipeline.identity import resolve_composition

    documents = {
        musicbrainz_lookup_url(BAND_MBID): artist_payload(
            BAND_MBID,
            "The Band",
            kind="Group",
            relations=[member_relation(FRONT_MBID, "Front", ["lead vocals"])],
        ),
        musicbrainz_lookup_url(FRONT_MBID): artist_payload(
            FRONT_MBID, "Front", gender="non-binary"
        ),
    }
    fronts, evidence = enricher_for(documents).composition_evidence(BAND_MBID)
    composition = resolve_composition(fronts, evidence)

    assert composition is not None
    assert composition.sourced_front_genders == frozenset({Gender.NONBINARY})
    assert composition.female_fronted is None


def test_a_front_person_with_no_upstream_claim_stays_unknown() -> None:
    documents = {
        musicbrainz_lookup_url(BAND_MBID): artist_payload(
            BAND_MBID,
            "The Band",
            kind="Group",
            relations=[member_relation(FRONT_MBID, "Front", ["lead vocals"])],
        ),
        musicbrainz_lookup_url(FRONT_MBID): artist_payload(FRONT_MBID, "Front"),
    }
    fronts, _ = enricher_for(documents).composition_evidence(BAND_MBID)
    assert fronts[0].identity.gender is Gender.UNKNOWN


def test_a_solo_artists_band_memberships_are_not_their_lineup() -> None:
    payload = json.loads(
        artist_payload(
            SOLO_MBID,
            "Solo",
            kind="Person",
            relations=[member_relation(BAND_MBID, "The Band", ["lead vocals"])],
        )
    )
    assert parse_musicbrainz_fronting(payload) == []


def test_a_group_with_no_fronting_role_sources_no_composition() -> None:
    documents = {
        musicbrainz_lookup_url(BAND_MBID): artist_payload(
            BAND_MBID,
            "The Band",
            kind="Group",
            relations=[member_relation(DRUMMER_MBID, "Drummer", ["drums"])],
        )
    }
    assert enricher_for(documents).composition_evidence(BAND_MBID) == ([], [])


def test_the_fronting_lineup_is_capped() -> None:
    members = [
        member_relation(
            f"{i}{i}{i}{i}{i}{i}{i}{i}-1111-4111-8111-111111111111", f"P{i}", ["vocals"]
        )
        for i in range(1, 9)
    ]
    documents = {
        musicbrainz_lookup_url(BAND_MBID): artist_payload(
            BAND_MBID, "The Band", kind="Group", relations=members
        )
    }
    fetcher = FakeFetcher(documents)
    enricher = MusicBrainzEnricher(fetcher, retrieved_at=RETRIEVED, max_front_people=2)
    fronts, _ = enricher.composition_evidence(BAND_MBID)
    assert len(fronts) == 2


@pytest.mark.parametrize(
    ("role", "fronting"),
    [("lead vocals", True), ("Backing Vocals", True), ("frontman", True), ("drums", False)],
)
def test_fronting_roles_are_read_from_what_the_source_stated(role: str, fronting: bool) -> None:
    assert is_fronting_role(role) is fronting


def test_a_relation_missing_its_artist_is_skipped() -> None:
    payload = json.loads(
        json.dumps(
            {
                "type": "Group",
                "relations": [
                    {"type": "member of band", "attributes": ["vocals"]},
                    {"type": "member of band", "attributes": ["vocals"], "artist": {"id": "x"}},
                    "not-an-object",
                ],
            }
        )
    )
    assert parse_musicbrainz_fronting(payload) == []


# --- payload plumbing -------------------------------------------------------


def test_a_wikidata_link_is_only_trusted_on_wikidata() -> None:
    hostile = json.loads(
        json.dumps(
            {"relations": [{"type": "wikidata", "url": {"resource": "https://evil.test/Q42"}}]}
        )
    )
    assert parse_wikidata_link(hostile) is None


def test_an_entity_document_for_a_different_subject_is_not_read() -> None:
    payload = json.loads(wikidata_payload("Q900006", "Q6581072"))
    assert (
        parse_wikidata_entity(payload, "Q999999", "https://www.wikidata.org/wiki/Q999999", "d")
        is None
    )


def test_entity_data_url_is_machine_readable_but_the_citation_is_not() -> None:
    assert wikidata_entity_data_url("Q42").endswith("Special:EntityData/Q42.json")


@pytest.mark.parametrize(
    ("value", "expected"), [(SOLO_MBID, True), (SOLO_MBID.upper(), True), ("Boygenius", False)]
)
def test_an_artist_key_is_recognisable_as_an_mbid_or_a_name(value: str, expected: bool) -> None:
    assert looks_like_mbid(value) is expected


def test_last_fm_is_asked_by_mbid_when_there_is_one_and_by_name_otherwise() -> None:
    """The bug this fixes: an `mbid=` query carrying a name is a 400, mid-ingest."""
    assert artist_query(SOLO_MBID) == {"mbid": SOLO_MBID}
    assert artist_query("Quiet Cartography") == {"artist": "Quiet Cartography", "autocorrect": "0"}


def test_similar_artists_carry_a_display_name_alongside_their_key() -> None:
    payload = {
        "similarartists": {
            "artist": [
                {"mbid": SOLO_MBID, "name": "Quiet Cartography", "match": "0.9"},
                {"name": "No Mbid Here", "match": "0.4"},
                {"name": "", "match": "0.1"},
            ]
        }
    }
    named = parse_similar_named(payload)

    assert [s.artist_id for s in named] == [SOLO_MBID, "No Mbid Here"]
    assert named[0].name == "Quiet Cartography"
    assert parse_similar(payload) == [(SOLO_MBID, 0.9), ("No Mbid Here", 0.4)]


def test_the_user_agent_carries_the_operators_contact() -> None:
    assert "someone@example.org" in build_user_agent("someone@example.org")
    assert "github.com" in build_user_agent("")


def test_a_cached_response_costs_no_request() -> None:
    with Cache(":memory:") as cache:
        cache.put_cached_response("https://musicbrainz.org/x", "{}", RETRIEVED)
        fetcher = CachedHttpFetcher(cache, user_agent="test/1.0 ( x )")
        assert fetcher("https://musicbrainz.org/x") == "{}"


def test_a_fetcher_without_a_user_agent_is_refused() -> None:
    with Cache(":memory:") as cache, pytest.raises(ValueError, match="User-Agent"):
        CachedHttpFetcher(cache, user_agent="   ")


# --- discovery: the step that makes a live world non-empty ------------------


class FakeSimilarity:
    def __init__(self, edges: dict[str, list[SimilarArtist]]) -> None:
        self.edges = edges

    def similar_artists_named(self, artist_id: str) -> list[SimilarArtist]:
        return list(self.edges.get(artist_id, []))


def profile_of(plays: dict[str, int]) -> object:
    from pipeline.ingest import build_profile

    scrobbles = [
        Scrobble(artist_id=aid, artist_name=aid.title(), track=f"t{i}", ts=1000 + i)
        for aid, count in plays.items()
        for i in range(count)
    ]
    return build_profile("listener", scrobbles)


def test_discovery_proposes_only_artists_the_listener_has_not_played() -> None:
    profile = profile_of({"alpha": 3, "beta": 1})
    source = FakeSimilarity(
        {
            "alpha": [SimilarArtist("gamma", "Gamma", 0.9), SimilarArtist("beta", "Beta", 0.8)],
            "beta": [SimilarArtist("gamma", "Gamma", 0.5), SimilarArtist("delta", "Delta", 0.4)],
        }
    )
    found = discover_candidates(profile, source)  # type: ignore[arg-type]

    assert [c.artist_id for c in found] == ["gamma", "delta"]
    assert found[0].name == "Gamma"


def test_an_artist_keyed_two_ways_is_still_recognised_as_known() -> None:
    """The regression: a 462-play artist arrived MBID-keyed and looked brand new.

    Scrobbles gave a bare name, the similar-artists payload gave an MBID for the
    same act, and an id-only exclusion saw two different artists.
    """
    profile = profile_of({"Quiet Cartography": 4, "beta": 1})
    source = FakeSimilarity(
        {
            "Quiet Cartography": [
                SimilarArtist(SOLO_MBID, "Quiet Cartography", 0.9),  # same act, MBID-keyed
                SimilarArtist(OTHER_MBID, "Genuinely New", 0.5),
            ]
        }
    )
    found = discover_candidates(profile, source, seeds=1)  # type: ignore[arg-type]

    assert [c.name for c in found] == ["Genuinely New"]


def test_the_recommender_applies_the_same_alias_guard() -> None:
    """Belt and braces: a catalog row that slipped in earlier is still excluded."""
    from recommender.hybrid import recommend

    profile = profile_of({"Quiet Cartography": 4})
    catalog = {
        SOLO_MBID: Artist(artist_id=SOLO_MBID, name="Quiet Cartography", tags=("folk",)),
        OTHER_MBID: Artist(artist_id=OTHER_MBID, name="Genuinely New", tags=("folk",)),
    }
    recs = recommend(profile, catalog, FixtureLastfm({}, {}, {}), k=10)  # type: ignore[arg-type]

    assert [r.artist.name for r in recs] == ["Genuinely New"]


def test_discovery_is_deterministic_and_bounded() -> None:
    profile = profile_of({"alpha": 2, "beta": 2})
    source = FakeSimilarity(
        {
            "alpha": [SimilarArtist(f"c{i}", f"C{i}", 1.0 - i / 10) for i in range(5)],
            "beta": [SimilarArtist(f"d{i}", f"D{i}", 1.0 - i / 10) for i in range(5)],
        }
    )
    first = discover_candidates(profile, source, seeds=1, per_seed=2, limit=2)  # type: ignore[arg-type]
    second = discover_candidates(profile, source, seeds=1, per_seed=2, limit=2)  # type: ignore[arg-type]

    assert [c.artist_id for c in first] == [c.artist_id for c in second]
    assert len(first) == 2


def test_one_unenrichable_candidate_does_not_sink_the_others() -> None:
    class HalfBrokenTags(FixtureLastfm):
        def artist_tags(self, artist_id: str) -> tuple[str, ...]:
            if artist_id == "broken":
                raise RuntimeError("upstream said no")
            return super().artist_tags(artist_id)

    from pipeline.enrich import FixtureEnricher

    source = HalfBrokenTags({}, {"fine": ("dream pop",)}, {})
    catalog = enrich_candidates(
        [Candidate("broken", "Broken", 0.9), Candidate("fine", "Fine", 0.5)],
        source,
        FixtureEnricher({}, {}),
    )
    assert list(catalog) == ["fine"]


# --- one artist's bad day is not the run's ----------------------------------


@pytest.mark.parametrize(
    ("status", "exc_name", "retry"),
    [
        (None, "ReadTimeout", True),
        (None, "ConnectionError", True),
        (503, "HTTPError", True),
        (404, "HTTPError", False),
        (400, "HTTPError", False),
        (429, "HTTPError", False),
        (None, "TooManyRedirects", False),
    ],
)
def test_only_an_unanswered_request_is_worth_retrying(
    status: int | None, exc_name: str, retry: bool
) -> None:
    """A 4xx is an answer; re-sending it spends a rate-limit slot to hear it again."""
    from pipeline.lastfm import is_transient_failure

    assert is_transient_failure(status, exc_name) is retry


def test_a_failed_artist_is_skipped_not_fatal() -> None:
    """The regression: one `ReadTimeout` used to discard a whole live ingest."""
    from pipeline.enrich import FixtureEnricher
    from pipeline.lastfm import LastfmRequestError

    class OneBadArtist(FixtureLastfm):
        def artist_tags(self, artist_id: str) -> tuple[str, ...]:
            if artist_id == "beta":
                raise LastfmRequestError("Last.fm request failed (ReadTimeout)")
            return super().artist_tags(artist_id)

    scrobbles = {
        "listener": [
            Scrobble(artist_id="alpha", artist_name="Alpha", track="one", ts=1),
            Scrobble(artist_id="beta", artist_name="Beta", track="two", ts=2),
            Scrobble(artist_id="gamma", artist_name="Gamma", track="three", ts=3),
        ]
    }
    source = OneBadArtist(scrobbles, {"alpha": ("folk",), "gamma": ("pop",)}, {})

    profile, catalog = ingest("listener", source, FixtureEnricher({}, {}), limit=10)

    assert sorted(catalog) == ["alpha", "gamma"]
    # …and the skipped artist is still *known*, so it is never recommended back.
    assert "beta" in profile.known_artist_ids


# --- the cached world a recommendation surface reads ------------------------


def test_the_read_path_needs_no_credential_and_no_socket() -> None:
    """`wad recommend --user` replays what ingest stored; a miss is an empty answer."""
    from pipeline.lastfm import CachedLastfm, cache_key

    params = {"method": "artist.getsimilar", "artist": "Alpha", "autocorrect": "0"}
    with Cache(":memory:") as cache:
        cache.put_cached_response(
            cache_key(params),
            json.dumps({"similarartists": {"artist": [{"name": "Beta", "match": "0.7"}]}}),
            RETRIEVED,
        )
        cache.put_scrobbles(
            "listener", [Scrobble(artist_id="alpha", artist_name="Alpha", track="t", ts=5)]
        )
        replay = CachedLastfm(cache)

        assert replay.similar_artists("Alpha") == [("Beta", 0.7)]
        assert replay.similar_artists("Never Fetched") == []
        assert replay.artist_tags("Never Fetched") == ()
        assert [s.artist_id for s in replay.recent_scrobbles("listener")] == ["alpha"]
        assert replay.scrobbles_since("listener", since_ts=10) == []


def test_a_replayed_response_that_is_not_json_is_an_empty_answer() -> None:
    from pipeline.lastfm import CachedLastfm, cache_key

    params = {"method": "artist.gettoptags", "artist": "Alpha", "autocorrect": "0"}
    with Cache(":memory:") as cache:
        cache.put_cached_response(cache_key(params), "<html>", RETRIEVED)
        assert CachedLastfm(cache).artist_tags("Alpha") == ()


def test_the_cache_key_never_carries_the_credential() -> None:
    """Module-level now, because the replay reader has no key to reproduce."""
    from pipeline.lastfm import cache_key

    assert "api_key" not in cache_key({"method": "artist.getsimilar", "artist": "Alpha"})


def test_a_synced_world_survives_a_restart() -> None:
    """Ingest once, then rebuild profile + catalog from the cache alone."""
    from pipeline.enrich import FixtureEnricher

    scrobbles = {
        "listener": [
            Scrobble(artist_id="alpha", artist_name="Alpha", track="one", ts=1_000),
            Scrobble(artist_id="alpha", artist_name="Alpha", track="two", ts=1_001),
            Scrobble(artist_id="beta", artist_name="Beta", track="three", ts=1_002),
        ]
    }
    source = FixtureLastfm(scrobbles, {"alpha": ("dream pop",), "beta": ("folk",)}, {})

    with Cache(":memory:") as cache:
        ingest("listener", source, FixtureEnricher({}, {}), cache=cache, fetched_at=RETRIEVED)
        profile = profile_from_cache(cache, "listener")
        catalog = catalog_from_cache(cache)

    assert profile.username == "listener"
    assert profile.play_counts == {"alpha": 2.0, "beta": 1.0}
    assert profile.tags["alpha"] == ("dream pop",)
    assert sorted(catalog) == ["alpha", "beta"]


def test_enrichment_can_be_bounded_without_narrowing_the_profile() -> None:
    """The bounded artists still count as *known*, so they are never recommended."""
    from pipeline.enrich import FixtureEnricher

    scrobbles = {
        "listener": [
            Scrobble(artist_id="alpha", artist_name="Alpha", track=f"t{i}", ts=1_000 + i)
            for i in range(3)
        ]
        + [Scrobble(artist_id="beta", artist_name="Beta", track="one", ts=2_000)]
    }
    source = FixtureLastfm(scrobbles, {"alpha": ("dream pop",), "beta": ("folk",)}, {})

    profile, catalog = ingest("listener", source, FixtureEnricher({}, {}), limit=10, enrich_top=1)

    assert list(catalog) == ["alpha"]
    assert profile.known_artist_ids == frozenset({"alpha", "beta"})
