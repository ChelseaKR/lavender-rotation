"""Enrichment adapters: turn external metadata into *permitted-source evidence*.

Each parser validates the untrusted external payload before trusting it (security
posture: cache-poisoning resistance — RESPONSIBLE-TECH-AUDITS §F) and emits
:class:`~pipeline.identity.IdentityEvidence`, which the resolver alone turns into
labels. Parsers are pure and unit-tested from fixtures; live fetches are thin
wrappers excluded from coverage.

Provenance is preserved end to end: every emitted evidence carries the source
kind, a citation, and the fetch date.

Two implementations of :class:`EnrichmentSource`:

* :class:`FixtureEnricher` — offline, backed by pre-parsed evidence. Every test
  and the demo world use it, so the whole system runs with no network.
* :class:`MusicBrainzEnricher` — the live one (FIX-01). It reads MusicBrainz's
  ``gender`` field and, when the MusicBrainz record links to Wikidata, that
  entity's P21 claim. Both are public, citable, per-artist claims; neither is a
  bulk identity dataset, and nothing fetched here is ever redistributed
  (``docs/audits/identity-data-ethics.md``).

**Entity resolution is not identity inference, and is gated like it matters.**
Last.fm hands us an MBID for some artists and a bare name for the rest. Turning
a name into a MusicBrainz record is a *lookup*, not a claim about a person — but
a wrong lookup would attach a stranger's sourced gender to an artist, which is
the same harm the no-inference rule exists to prevent, arriving by a different
road. So :func:`parse_musicbrainz_search` accepts a match only when the record's
name is exactly the queried name and exactly one record qualifies. An ambiguous
name resolves to nothing, and the artist stays ``UNKNOWN`` — first-class, never
down-ranked, and honest about what we actually know.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional, Protocol

from pipeline.http import Fetcher, HttpFetchError
from pipeline.identity import IdentityEvidence, resolve_identity
from pipeline.lastfm import looks_like_mbid
from pipeline.models import FrontPerson, IdentityLabel, SourceKind

log = logging.getLogger("wad.enrich")

# MusicBrainz gender field — the only values we accept; anything else is ignored
# (treated as unknown) rather than coerced.
_MB_GENDER_ALLOWED = {"male", "female", "other", "non-binary"}

#: Role words a source may use to state that someone fronts an act. Matched
#: against *the role the source itself stated*, never against anything about the
#: person — this decides whether a lineup entry is a front-person, and says
#: nothing whatsoever about their gender.
_FRONTING_ROLE_MARKERS = ("vocal", "front", "lead singer")


def is_fronting_role(role: str) -> bool:
    """True if a source-stated lineup role describes fronting the act."""
    text = role.strip().lower()
    return any(marker in text for marker in _FRONTING_ROLE_MARKERS)


class EnrichmentSource(Protocol):
    """Yields permitted-source evidence for an artist."""

    def gender_evidence(self, artist_id: str) -> list[IdentityEvidence]: ...

    def orientation_evidence(self, artist_id: str) -> list[IdentityEvidence]:
        """Sourced sexual-orientation claims (ADR 0011). Empty is the norm."""
        ...

    def composition_evidence(
        self, artist_id: str
    ) -> tuple[list[FrontPerson], list[IdentityEvidence]]: ...


def parse_musicbrainz_gender(
    payload: object, citation: str, retrieved_at: str
) -> Optional[IdentityEvidence]:
    """Parse a MusicBrainz artist object's ``gender`` field, with validation."""
    if not isinstance(payload, dict):
        raise ValueError("musicbrainz payload must be an object")
    gender = payload.get("gender")
    if not isinstance(gender, str):
        return None
    value = gender.strip().lower()
    if value not in _MB_GENDER_ALLOWED:
        return None  # unrecognised → unknown, never guessed
    return IdentityEvidence(
        kind=SourceKind.MUSICBRAINZ_GENDER,
        value=value,
        citation=citation,
        retrieved_at=retrieved_at,
    )


def _first_claim_qid(
    payload: object, prop: str, kind: SourceKind, citation: str, retrieved_at: str
) -> Optional[IdentityEvidence]:
    """The first item-valued claim for ``prop``, validated, as evidence.

    Shared by P21 and P91 (ADR 0011): the document shape is identical and the
    two axes differ only in which property is read and what kind of claim the
    result is. Only the *first* statement is taken — a person with several
    recorded values is not something to summarise, and the resolver's job is to
    normalise one asserted value, not to reconcile a list.
    """
    if not isinstance(payload, dict):
        raise ValueError("wikidata payload must be an object")
    claims = payload.get("claims", {})
    if not isinstance(claims, dict):
        return None
    statements = claims.get(prop, [])
    if not isinstance(statements, list) or not statements:
        return None
    try:
        qid = statements[0]["mainsnak"]["datavalue"]["value"]["id"]
    except (KeyError, TypeError, IndexError):
        return None
    if not isinstance(qid, str) or not qid.startswith("Q"):
        return None
    return IdentityEvidence(kind=kind, value=qid, citation=citation, retrieved_at=retrieved_at)


def parse_wikidata_p21(
    payload: object, citation: str, retrieved_at: str
) -> Optional[IdentityEvidence]:
    """Parse a Wikidata entity's P21 ('sex or gender') claim into a QID evidence."""
    return _first_claim_qid(payload, "P21", SourceKind.WIKIDATA_P21, citation, retrieved_at)


def parse_discogs_lineup(
    payload: object, citation: str, retrieved_at: str
) -> tuple[list[FrontPerson], list[IdentityEvidence]]:
    """Parse a Discogs lineup into sourced front-people + composition evidence.

    Each band member can carry their *own* sourced gender evidence (e.g. a member
    who has a self-statement); a member without it stays unknown. Returns the
    front-people plus one band-composition evidence row attesting the lineup.
    """
    if not isinstance(payload, dict):
        raise ValueError("discogs payload must be an object")
    members = payload.get("members", [])
    if not isinstance(members, list):
        return [], []
    fronts: list[FrontPerson] = []
    for m in members:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "")).strip().lower()
        # "Fronting" = lead vocals / frontperson, per the source's stated role.
        if not is_fronting_role(role):
            continue
        member_evidence: list[IdentityEvidence] = []
        statement = m.get("identity_statement")
        if isinstance(statement, dict) and statement.get("value"):
            member_evidence.append(
                IdentityEvidence(
                    kind=SourceKind.ARTIST_STATEMENT,
                    value=str(statement["value"]).strip().lower(),
                    citation=str(statement.get("citation", citation)),
                    retrieved_at=retrieved_at,
                )
            )
        fronts.append(
            FrontPerson(
                name=str(m.get("name", "")).strip(),
                role=role or "vocals",
                identity=resolve_identity(member_evidence),
            )
        )
    composition_evidence = (
        [
            IdentityEvidence(
                kind=SourceKind.DISCOGS_LINEUP,
                value="lineup",
                citation=citation,
                retrieved_at=retrieved_at,
            )
        ]
        if fronts
        else []
    )
    return fronts, composition_evidence


class FixtureEnricher:
    """Offline enrichment backed by pre-parsed evidence dicts."""

    def __init__(
        self,
        gender: dict[str, list[IdentityEvidence]],
        composition: dict[str, tuple[list[FrontPerson], list[IdentityEvidence]]],
        orientation: Optional[dict[str, list[IdentityEvidence]]] = None,
    ) -> None:
        self._gender = gender
        self._composition = composition
        self._orientation = orientation or {}

    def gender_evidence(self, artist_id: str) -> list[IdentityEvidence]:
        return list(self._gender.get(artist_id, []))

    def orientation_evidence(self, artist_id: str) -> list[IdentityEvidence]:
        return list(self._orientation.get(artist_id, []))

    def composition_evidence(
        self, artist_id: str
    ) -> tuple[list[FrontPerson], list[IdentityEvidence]]:
        return self._composition.get(artist_id, ([], []))


# --- The live enricher (FIX-01) ---------------------------------------------

MUSICBRAINZ_WS = "https://musicbrainz.org/ws/2/artist"
MUSICBRAINZ_ARTIST_URL = "https://musicbrainz.org/artist/{mbid}"
WIKIDATA_ENTITY_URL = "https://www.wikidata.org/wiki/{qid}"
WIKIDATA_ENTITY_DATA_URL = "https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"

#: One lookup serves both halves of the protocol: ``url-rels`` carries the
#: Wikidata link and ``artist-rels`` the lineup, so ``gender_evidence`` and
#: ``composition_evidence`` for the same artist share one cached response.
_LOOKUP_INC = "url-rels+artist-rels"
_SEARCH_LIMIT = 10

#: MusicBrainz search scores a candidate 0-100. Only a *perfect* score is
#: considered, and even then the name must match exactly — see the module
#: docstring on why entity resolution is gated this hard.
_MIN_SEARCH_SCORE = 100

#: How many sourced front-people one act may contribute. A bound on fan-out,
#: not a claim: a lineup longer than this is truncated in the order the source
#: listed it, which is why it is generous relative to real fronting lineups.
MAX_FRONT_PEOPLE = 6

#: A Wikidata link is only trusted when it is an address on Wikidata itself.
_WIKIDATA_RESOURCE = re.compile(r"^https?://www\.wikidata\.org/wiki/(Q[1-9][0-9]*)$")


def musicbrainz_search_url(name: str) -> str:
    """Search URL for an artist name (double quotes stripped, they break Lucene)."""
    cleaned = name.strip().replace('"', "")
    query = urllib.parse.urlencode(
        {"query": f'artist:"{cleaned}"', "fmt": "json", "limit": str(_SEARCH_LIMIT)}
    )
    return f"{MUSICBRAINZ_WS}?{query}"


def musicbrainz_lookup_url(mbid: str) -> str:
    """Lookup URL for one MusicBrainz artist, with the relations we read."""
    query = urllib.parse.urlencode({"fmt": "json", "inc": _LOOKUP_INC})
    return f"{MUSICBRAINZ_WS}/{urllib.parse.quote(mbid)}?{query}"


def wikidata_entity_data_url(qid: str) -> str:
    """Machine-readable URL for a Wikidata entity (the citation stays the /wiki/ one)."""
    return WIKIDATA_ENTITY_DATA_URL.format(qid=urllib.parse.quote(qid))


def _exact_match_id(entry: object, wanted: str) -> Optional[str]:
    """The MBID of one search hit, if it is an exact, perfect-score match."""
    if not isinstance(entry, dict):
        return None
    if str(entry.get("name", "")).strip().casefold() != wanted:
        return None
    try:
        score = int(entry.get("score", 0))
    except (TypeError, ValueError):
        return None
    if score < _MIN_SEARCH_SCORE:
        return None
    mbid = str(entry.get("id", "")).strip().lower()
    return mbid if looks_like_mbid(mbid) else None


def parse_musicbrainz_search(payload: object, queried_name: str) -> Optional[str]:
    """Resolve a name to *one unambiguous* MBID, or ``None``.

    Returning ``None`` for an ambiguous name is the point, not a shortcoming.
    Two different artists genuinely share a name often enough (and MusicBrainz
    disambiguates them by a free-text comment we deliberately do not try to
    interpret) that picking the higher-scoring one would amount to guessing
    which person a claim is about. The caller treats ``None`` as "no evidence",
    which resolves to first-class ``UNKNOWN``.
    """
    if not isinstance(payload, dict):
        raise ValueError("musicbrainz search payload must be an object")
    found = payload.get("artists", [])
    wanted = queried_name.strip().casefold()
    if not isinstance(found, list) or not wanted:
        return None
    candidates = {mbid for entry in found if (mbid := _exact_match_id(entry, wanted)) is not None}
    return candidates.pop() if len(candidates) == 1 else None


def parse_wikidata_link(payload: object) -> Optional[str]:
    """Extract the Wikidata Q-number a MusicBrainz artist links to, if any."""
    if not isinstance(payload, dict):
        raise ValueError("musicbrainz payload must be an object")
    relations = payload.get("relations", [])
    if not isinstance(relations, list):
        return None
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        if str(relation.get("type", "")).strip().lower() != "wikidata":
            continue
        target = relation.get("url", {})
        resource = str(target.get("resource", "")).strip() if isinstance(target, dict) else ""
        match = _WIKIDATA_RESOURCE.match(resource)
        if match:
            return match.group(1)
    return None


def _unwrap_entity(payload: object, qid: str) -> Optional[dict[str, object]]:
    """The entity for ``qid`` inside a Special:EntityData document, if present."""
    if not isinstance(payload, dict):
        raise ValueError("wikidata entity payload must be an object")
    entities = payload.get("entities", {})
    if not isinstance(entities, dict):
        return None
    entity = entities.get(qid)
    return entity if isinstance(entity, dict) else None


def parse_wikidata_entity(
    payload: object, qid: str, citation: str, retrieved_at: str
) -> Optional[IdentityEvidence]:
    """Unwrap a Special:EntityData document and read its P21 claim."""
    entity = _unwrap_entity(payload, qid)
    return None if entity is None else parse_wikidata_p21(entity, citation, retrieved_at)


def parse_wikidata_entity_orientation(
    payload: object, qid: str, citation: str, retrieved_at: str
) -> Optional[IdentityEvidence]:
    """The same document's P91 ("sexual orientation") claim (ADR 0011).

    Free, in request terms: the entity is already fetched for P21, so the second
    axis costs nothing upstream and asks nobody anything new.
    """
    entity = _unwrap_entity(payload, qid)
    return None if entity is None else parse_wikidata_p91(entity, citation, retrieved_at)


def parse_wikidata_p91(
    payload: object, citation: str, retrieved_at: str
) -> Optional[IdentityEvidence]:
    """Parse a Wikidata entity's P91 ('sexual orientation') claim into QID evidence."""
    return _first_claim_qid(payload, "P91", SourceKind.WIKIDATA_P91, citation, retrieved_at)


@dataclass(frozen=True)
class BandMember:
    """One sourced lineup entry: who, and the role the source stated for them."""

    mbid: str
    name: str
    role: str


def parse_musicbrainz_fronting(payload: object) -> list[BandMember]:
    """Front-people from a group's ``member of band`` relations.

    Only relations the source itself marks as fronting roles are returned (see
    :func:`is_fronting_role`), and only for an entity MusicBrainz classifies as
    a group — a solo artist's band memberships describe *someone else's*
    lineup, and reading them as this artist's would invent a composition claim
    the source never made.
    """
    if not isinstance(payload, dict):
        raise ValueError("musicbrainz payload must be an object")
    if str(payload.get("type", "")).strip().lower() != "group":
        return []
    relations = payload.get("relations", [])
    if not isinstance(relations, list):
        return []
    members: list[BandMember] = []
    for relation in relations:
        member = _fronting_member(relation)
        if member is not None:
            members.append(member)
    return members


def _fronting_member(relation: object) -> Optional[BandMember]:
    """One ``member of band`` relation, if it states a fronting role."""
    if not isinstance(relation, dict):
        return None
    if str(relation.get("type", "")).strip().lower() != "member of band":
        return None
    stated = relation.get("attributes", [])
    roles = [str(item).strip().lower() for item in stated] if isinstance(stated, list) else []
    fronting = next((role for role in roles if is_fronting_role(role)), None)
    if fronting is None:
        return None
    related = relation.get("artist", {})
    if not isinstance(related, dict):
        return None
    mbid = str(related.get("id", "")).strip().lower()
    person = str(related.get("name", "")).strip()
    if not looks_like_mbid(mbid) or not person:
        return None
    return BandMember(mbid=mbid, name=person, role=fronting)


class MusicBrainzEnricher:
    """Live enrichment from MusicBrainz, corroborated by Wikidata where linked.

    Every fetch goes through an injected :class:`~pipeline.http.Fetcher`, which
    is what keeps this class unit-testable from recorded payloads and keeps the
    ``requests`` import in the one allowlisted egress module.

    **An upstream failure is unknown, not an error.** A timeout, a 503, or a
    malformed payload yields no evidence, and no evidence resolves to
    first-class ``UNKNOWN`` — the same answer as an artist upstream has no claim
    about. Raising instead would abort a whole ingest over one flaky request,
    and silently *retrying* until something answered would be worse: the value
    of this pipeline is that a label is either sourced or absent.
    """

    def __init__(
        self,
        fetch: Fetcher,
        *,
        retrieved_at: str,
        max_front_people: int = MAX_FRONT_PEOPLE,
    ) -> None:
        self._fetch = fetch
        self.retrieved_at = retrieved_at
        self.max_front_people = max_front_people
        # Within one run an artist is looked up twice (once per protocol
        # method); the HTTP cache already makes the second free, this makes it
        # free without a disk read.
        self._resolved: dict[str, Optional[str]] = {}

    # -- protocol ----------------------------------------------------------
    def gender_evidence(self, artist_id: str) -> list[IdentityEvidence]:
        mbid = self.resolve_mbid(artist_id)
        if mbid is None:
            return []
        payload = self._artist_payload(mbid)
        return [] if payload is None else self._evidence_for(mbid, payload)

    def orientation_evidence(self, artist_id: str) -> list[IdentityEvidence]:
        """Sourced orientation claims (ADR 0011) — Wikidata P91 only, upstream.

        MusicBrainz has no orientation field and neither does Discogs, so the
        live path can offer exactly one source here. An artist's own cited
        words are the higher-trust source, but nothing publishes them in a
        machine-readable form; they reach the resolver through the corrections
        ledger (``wad corrections --artist … --value queer --citation …``),
        which is also the route by which a person can get their own entry
        right when a registry has it wrong.
        """
        mbid = self.resolve_mbid(artist_id)
        if mbid is None:
            return []
        payload = self._artist_payload(mbid)
        if payload is None:
            return []
        qid = parse_wikidata_link(payload)
        if qid is None:
            return []
        entity = self._json(wikidata_entity_data_url(qid))
        if entity is None:
            return []
        claimed = parse_wikidata_entity_orientation(
            entity, qid, WIKIDATA_ENTITY_URL.format(qid=qid), self.retrieved_at
        )
        return [] if claimed is None else [claimed]

    def composition_evidence(
        self, artist_id: str
    ) -> tuple[list[FrontPerson], list[IdentityEvidence]]:
        mbid = self.resolve_mbid(artist_id)
        if mbid is None:
            return [], []
        payload = self._artist_payload(mbid)
        if payload is None:
            return [], []
        members = parse_musicbrainz_fronting(payload)[: self.max_front_people]
        if not members:
            return [], []
        fronts = [
            FrontPerson(name=member.name, role=member.role, identity=self._member_label(member))
            for member in members
        ]
        stated = IdentityEvidence(
            kind=SourceKind.MUSICBRAINZ_RELATIONSHIP,
            value="member of band",
            citation=MUSICBRAINZ_ARTIST_URL.format(mbid=mbid),
            retrieved_at=self.retrieved_at,
        )
        return fronts, [stated]

    # -- internals ---------------------------------------------------------
    def resolve_mbid(self, artist_id: str) -> Optional[str]:
        """The MusicBrainz id for a Last.fm artist key, or ``None`` if ambiguous."""
        if artist_id in self._resolved:
            return self._resolved[artist_id]
        resolved: Optional[str] = None
        if looks_like_mbid(artist_id):
            resolved = artist_id.strip().lower()
        else:
            payload = self._json(musicbrainz_search_url(artist_id))
            if payload is not None:
                resolved = parse_musicbrainz_search(payload, artist_id)
        self._resolved[artist_id] = resolved
        return resolved

    def _artist_payload(self, mbid: str) -> Optional[dict[str, object]]:
        return self._json(musicbrainz_lookup_url(mbid))

    def _evidence_for(self, mbid: str, payload: dict[str, object]) -> list[IdentityEvidence]:
        """Every permitted claim upstream makes about one MusicBrainz artist."""
        found: list[IdentityEvidence] = []
        stated = parse_musicbrainz_gender(
            payload, MUSICBRAINZ_ARTIST_URL.format(mbid=mbid), self.retrieved_at
        )
        if stated is not None:
            found.append(stated)
        qid = parse_wikidata_link(payload)
        if qid is None:
            return found
        entity = self._json(wikidata_entity_data_url(qid))
        if entity is None:
            return found
        claimed = parse_wikidata_entity(
            entity, qid, WIKIDATA_ENTITY_URL.format(qid=qid), self.retrieved_at
        )
        if claimed is not None:
            found.append(claimed)
        return found

    def _member_label(self, member: BandMember) -> IdentityLabel:
        """A front-person's own sourced label — resolved exactly like a solo act's."""
        payload = self._artist_payload(member.mbid)
        if payload is None:
            return IdentityLabel()
        return resolve_identity(self._evidence_for(member.mbid, payload))

    def _json(self, url: str) -> Optional[dict[str, object]]:
        """Fetch and parse one document, or ``None`` if anything went wrong."""
        try:
            body = self._fetch(url)
        except HttpFetchError:
            log.warning("stage=enrich_upstream event=fetch_failed")
            return None
        try:
            document = json.loads(body)
        except json.JSONDecodeError:
            log.warning("stage=enrich_upstream event=malformed_payload")
            return None
        if not isinstance(document, dict):
            log.warning("stage=enrich_upstream event=unexpected_shape")
            return None
        return document
