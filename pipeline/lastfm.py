"""Last.fm scrobble/tag/similarity source, with rate-limit respect + caching.

Two implementations of :class:`ScrobbleSource`:

* :class:`LastfmClient` — the live HTTP client. It honors Last.fm's rate limit
  via :class:`RateLimiter` and caches every response in the local :class:`Cache`
  so repeat runs do not re-hit the API (legal/ops requirement). The actual
  network calls are excluded from unit coverage; the parsing they feed is tested.
* :class:`FixtureLastfm` — an offline, deterministic source built from a dict.
  Used by every test and by the dashboard's demo mode, so the whole system runs
  with no API key and no network.

**The API key.** Last.fm authenticates a read request with an ``api_key`` query
parameter — no header auth exists, and its docs reserve POST for write services
— so the credential is unavoidably part of the request URL. Two places that URL
must therefore never reach: the on-disk cache (see :meth:`LastfmClient.cache_key`)
and an exception message (see :class:`LastfmRequestError`). Both are asserted by
``tests/test_privacy.py``.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from pipeline.cache import Cache
from pipeline.models import Scrobble

log = logging.getLogger("lavender.lastfm")

#: An artist key is an MBID when Last.fm supplied one and the display name
#: otherwise (see :func:`parse_recent_tracks`), so every downstream caller needs
#: to tell the two apart — to pick a query parameter here, and to decide whether
#: a MusicBrainz lookup needs a search first (:mod:`pipeline.enrich`).
_MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def looks_like_mbid(value: str) -> bool:
    """True if an artist key is a MusicBrainz identifier rather than a name."""
    return bool(_MBID.match(value.strip()))


def artist_query(artist_id: str) -> dict[str, str]:
    """Identify an artist to Last.fm by MBID when we have one, by name otherwise.

    Last.fm's ``artist.*`` methods reject an ``mbid`` that is not one, with a
    400 — which, before this existed, turned every MBID-less scrobble (a large
    share of a real listening history) into a failed request that aborted the
    whole ingest. ``autocorrect=0`` keeps the key we asked about identical to
    the key the answer is stored under; the name came from Last.fm's own
    scrobble data, so there is nothing to correct.
    """
    key = artist_id.strip()
    return {"mbid": key} if looks_like_mbid(key) else {"artist": key, "autocorrect": "0"}


@dataclass(frozen=True)
class SimilarArtist:
    """One similar-artist edge, with the display name the id alone does not carry."""

    artist_id: str
    name: str
    match: float


class LastfmRequestError(RuntimeError):
    """A failed Last.fm request, rendered *without* the API key.

    ``requests`` puts the fully-expanded request URL into the message of every
    exception it raises — ``HTTPError`` from ``raise_for_status`` ("… for url:
    https://…?…&api_key=…"), and ``ConnectionError``/``Timeout`` alike ("… Max
    retries exceeded with url: /2.0/?…&api_key=…"). Since Last.fm's REST API
    takes the key as a query parameter and offers no header auth, letting one of
    those escape would put the credential into every traceback, crash report and
    error log downstream of it.

    So the live client catches them and re-raises this instead, ``from None`` so
    the leaking original is not chained into the rendered traceback either.
    """


#: Attempts per request. Two, not more: this client is rate-limited and runs
#: inside a job that already tolerates a failed artist, so the job of a retry
#: here is to absorb a single blip, not to keep trying until something answers.
MAX_ATTEMPTS = 2

#: Hard cap on pages one ``scrobbles_since`` call will walk. ``totalPages`` is an
#: integer supplied by the far end, and the loop's only stop condition was
#: reaching it: a glitched or hostile ``"totalPages": "999999999"`` walked
#: essentially forever at the rate limiter's one-request-per-second pace, with no
#: way to tell it apart from a genuinely long history. 2000 pages at the default
#: 200 scrobbles a page is 400,000 plays, comfortably past any real listening
#: history, and hitting it is logged rather than passed off as a complete sync.
#: A run that caps still resumes: the next call starts from the new watermark.
MAX_PAGES = 2_000

#: ``requests`` exception classes that mean "the request never got an answer".
_TRANSIENT_EXCEPTIONS = frozenset(
    {"ConnectionError", "Timeout", "ReadTimeout", "ConnectTimeout", "ChunkedEncodingError"}
)


def is_transient_failure(status: Optional[int], exc_name: str) -> bool:
    """Whether a failed request is worth exactly one more attempt.

    A timeout or a dropped connection is a blip — nothing about it says the
    request was wrong. A 4xx is an *answer*: the artist is unknown to Last.fm,
    or the key is bad. Re-sending it spends a rate-limit slot to be told the
    same thing. 429 is deliberately absent too: being told to slow down is not
    an invitation to immediately retry, and the fix for it is the limiter.
    """
    if status is None:
        return exc_name in _TRANSIENT_EXCEPTIONS
    return status >= 500


def redacted_failure_message(method: str, status: Optional[int], exc_name: str) -> str:
    """The one message shape :class:`LastfmRequestError` is ever built from.

    Deliberately assembled from three non-secret pieces — the API *method*, the
    HTTP status if there was a response, and the ``requests`` exception class
    name if there was not. Nothing derived from the request URL is admissible,
    because the key rides in that URL.
    """
    detail = f"HTTP {status}" if status is not None else exc_name
    return f"Last.fm request failed ({detail}) for method={method!r}"


@runtime_checkable
class ScrobbleSource(Protocol):
    """The listening-data interface the pipeline depends on."""

    def recent_scrobbles(self, username: str, limit: int = 200) -> list[Scrobble]: ...

    def scrobbles_since(
        self, username: str, since_ts: int = 0, page_size: int = 200
    ) -> list[Scrobble]:
        """Return every scrobble with ``ts > since_ts``, ascending, paginating as needed.

        The since-cursor + pagination make full-history ingest resumable: a
        caller persists the newest ``ts`` it has seen and passes it back in as
        ``since_ts`` on the next call to fetch only what's new (FIX-02).
        """
        ...

    def artist_tags(self, artist_id: str) -> tuple[str, ...]: ...

    def similar_artists(self, artist_id: str) -> list[tuple[str, float]]: ...


class NamedSimilaritySource(Protocol):
    """A similarity source that also carries display names.

    Kept separate from :class:`ScrobbleSource` rather than widened into it: the
    recommender only ever needs the edges, and only candidate discovery
    (:func:`pipeline.ingest.discover_candidates`) needs a name to show for an
    artist nobody has played yet. Splitting it keeps the offline fixture free of
    a method the offline world has no use for.
    """

    def similar_artists_named(self, artist_id: str) -> list[SimilarArtist]: ...


class RateLimiter:
    """Minimum-interval limiter. Clock + sleeper are injectable for testing.

    Last.fm asks for <= ~5 requests/second; the default 0.25 s interval stays
    comfortably under that. ``acquire`` blocks only as long as needed.
    """

    def __init__(
        self,
        min_interval: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleeper = sleeper
        self._next_allowed = 0.0

    def acquire(self) -> None:
        now = self._clock()
        wait = self._next_allowed - now
        if wait > 0:
            self._sleeper(wait)
            now = now + wait
        self._next_allowed = now + self.min_interval


#: Every Last.fm read goes to this one endpoint, distinguished by ``method``.
LASTFM_API_ROOT = "https://ws.audioscrobbler.com/2.0/"


def cache_key(params: Mapping[str, str]) -> str:
    """The cache identity of a Last.fm request — deliberately *without* the API key.

    The key is a credential, not part of what makes two requests the same
    request: the same method and arguments return the same response whoever
    asks. Including it would have written the secret into the ``http_cache``
    table in clear text, where it would outlive the process, the rotation of
    the key, and any log the operator thought to scrub.

    It lives at module scope because both the live client (which writes these
    rows) and :class:`CachedLastfm` (which reads them back with no credential to
    hand) have to agree on it exactly. Changing the key shape invalidates rows
    an earlier build wrote; that is the intended cost. Those rows simply miss
    and are re-fetched (or aged out by ``lavender refresh --ttl-days``).
    """
    query = urllib.parse.urlencode({**params, "format": "json"})
    return f"{LASTFM_API_ROOT}?{query}"


class FixtureLastfm:
    """A deterministic, offline :class:`ScrobbleSource` built from plain data."""

    def __init__(
        self,
        scrobbles: dict[str, list[Scrobble]],
        tags: dict[str, tuple[str, ...]],
        similar: dict[str, list[tuple[str, float]]],
    ) -> None:
        self._scrobbles = scrobbles
        self._tags = tags
        self._similar = similar

    def recent_scrobbles(self, username: str, limit: int = 200) -> list[Scrobble]:
        return list(self._scrobbles.get(username, []))[:limit]

    def scrobbles_since(
        self, username: str, since_ts: int = 0, page_size: int = 200
    ) -> list[Scrobble]:
        # `page_size` is accepted for Protocol parity; the fixture holds the
        # whole (small, offline) history in memory, so there is nothing to
        # actually paginate over — it simulates a fully-drained multi-page
        # fetch by simply returning everything newer than the cursor.
        ordered = sorted(self._scrobbles.get(username, []), key=lambda s: s.ts)
        return [s for s in ordered if s.ts > since_ts]

    def artist_tags(self, artist_id: str) -> tuple[str, ...]:
        return self._tags.get(artist_id, ())

    def similar_artists(self, artist_id: str) -> list[tuple[str, float]]:
        return list(self._similar.get(artist_id, []))


class CachedLastfm:
    """Replay-only :class:`ScrobbleSource`: answers from the local cache, never the network.

    What ``lavender recommend --user`` reads. Once ``lavender ingest`` has run, every tag
    and similarity response the recommender walks is already stored, so the read
    path needs no API key and opens no socket — which is the local-first posture
    taken literally: a cache miss is an empty answer, not a fetch.

    An empty answer is safe by construction. A missing similarity response
    contributes no collaborative edges and missing tags contribute no content
    signal, so an artist the cache never learned about simply does not surface;
    nothing is scored on a guess in its absence.
    """

    def __init__(self, cache: Cache) -> None:
        self.cache = cache

    def _replay(self, params: dict[str, str]) -> Optional[object]:
        import json

        body = self.cache.get_cached_response(cache_key(params))
        if body is None:
            return None
        try:
            payload: object = json.loads(body)
        except json.JSONDecodeError:
            return None
        problem = api_error(payload)
        if problem is not None:
            # A row written before `_get` learned to reject these. Replayed as
            # data it would read as an empty answer, which is what this class
            # returns for a miss anyway — but silently, so it is logged. Not
            # raised: this is the offline read path, whose whole contract is
            # that a missing response costs signal rather than the run.
            log.warning(
                "stage=lastfm event=cached_error_envelope method=%s",
                params.get("method", "?"),
            )
            return None
        return payload

    def recent_scrobbles(self, username: str, limit: int = 200) -> list[Scrobble]:
        return self.cache.get_scrobbles(username)[-limit:]

    def scrobbles_since(
        self, username: str, since_ts: int = 0, page_size: int = 200
    ) -> list[Scrobble]:
        return [s for s in self.cache.get_scrobbles(username) if s.ts > since_ts]

    def artist_tags(self, artist_id: str) -> tuple[str, ...]:
        payload = self._replay({"method": "artist.gettoptags", **artist_query(artist_id)})
        return () if payload is None else parse_top_tags(payload)

    def similar_artists(self, artist_id: str) -> list[tuple[str, float]]:
        payload = self._replay({"method": "artist.getsimilar", **artist_query(artist_id)})
        return [] if payload is None else parse_similar(payload)


class LastfmClient:  # pragma: no cover - live network path, verified via integration
    """Live Last.fm client. Network calls are integration-tested, not unit-gated."""

    API_ROOT = LASTFM_API_ROOT

    def __init__(
        self,
        api_key: str,
        cache: Cache,
        limiter: Optional[RateLimiter] = None,
        now_fn: Callable[[], str] = lambda: time.strftime("%Y-%m-%d"),
    ) -> None:
        if not api_key:
            raise ValueError("a Last.fm API key is required for the live client")
        self.api_key = api_key
        self.cache = cache
        self.limiter = limiter or RateLimiter()
        self._now = now_fn

    def cache_key(self, params: Mapping[str, str]) -> str:
        """This client's view of :func:`cache_key` — see there for why the key is absent."""
        return cache_key(params)

    def _get(self, params: dict[str, str]) -> str:
        key = self.cache_key(params)
        cached = self.cache.get_cached_response(key)
        if cached is not None:
            return cached
        body = self._request(params)
        # Checked *before* the write. Last.fm signals some failures with HTTP 200
        # and an error envelope, and caching that body pins the failure into the
        # local cache permanently — every later replay serves the error document
        # as though it were an answer, which reads downstream as "this artist has
        # no tags" or "this user has no plays".
        import json as _json

        try:
            problem = api_error(_json.loads(body))
        except _json.JSONDecodeError:
            problem = None  # not JSON at all: leave it to the parser's ValueError
        if problem is not None:
            raise LastfmRequestError(problem)
        self.cache.put_cached_response(key, body, self._now())
        return body

    def _request(self, params: dict[str, str]) -> str:
        """One request, retried once if it never got an answer."""
        import requests

        method = params.get("method", "?")
        for attempt in range(MAX_ATTEMPTS):
            self.limiter.acquire()
            # The key travels as a query parameter because that is what Last.fm's
            # REST docs specify for a read service — there is no header auth, and
            # POST is documented only for write services. It is handed to `requests`
            # at call time and never interpolated into a string this module holds,
            # stores, or renders; see `LastfmRequestError` for the other half.
            try:
                resp = requests.get(
                    self.API_ROOT,
                    params={**params, "api_key": self.api_key, "format": "json"},
                    timeout=15,
                )
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                exc_name = type(exc).__name__
                if attempt + 1 >= MAX_ATTEMPTS or not is_transient_failure(status, exc_name):
                    raise LastfmRequestError(
                        redacted_failure_message(method, status, exc_name)
                    ) from None
                log.warning("stage=lastfm event=retrying method=%s", method)
        raise LastfmRequestError(  # pragma: no cover - the loop returns or raises
            redacted_failure_message(method, None, "RetriesExhausted")
        )

    def recent_scrobbles(self, username: str, limit: int = 200) -> list[Scrobble]:
        import json

        body = self._get({"method": "user.getrecenttracks", "user": username, "limit": str(limit)})
        return parse_recent_tracks(json.loads(body))

    def scrobbles_since(
        self, username: str, since_ts: int = 0, page_size: int = 200
    ) -> list[Scrobble]:
        """Paginate user.getrecenttracks from a since-cursor until exhausted.

        Loops with ``from=<since_ts>``, ``limit=<page_size>``, ``page=<n>``,
        reading ``@attr.totalPages`` off each response to know when to stop.
        Rate limiting happens in ``_get`` via the shared ``RateLimiter``, so a
        full-history first sync naturally paces itself under Last.fm's limit.
        """
        import json

        out: list[Scrobble] = []
        page = 1
        total_pages = 1
        while page <= min(total_pages, MAX_PAGES):
            body = self._get(
                {
                    "method": "user.getrecenttracks",
                    "user": username,
                    "from": str(since_ts),
                    "limit": str(page_size),
                    "page": str(page),
                }
            )
            payload = json.loads(body)
            out.extend(parse_recent_tracks(payload))
            container = payload.get("recenttracks", {}) if isinstance(payload, dict) else {}
            attr = container.get("@attr", {}) if isinstance(container, dict) else {}
            try:
                total_pages = int(attr.get("totalPages", 1))
            except (TypeError, ValueError):
                total_pages = 1
            page += 1
        if total_pages > MAX_PAGES:
            log.warning(
                "stage=lastfm event=pagination_capped pages=%d cap=%d",
                total_pages,
                MAX_PAGES,
            )
        out.sort(key=lambda s: s.ts)
        return [s for s in out if s.ts > since_ts]

    def artist_tags(self, artist_id: str) -> tuple[str, ...]:
        import json

        body = self._get({"method": "artist.gettoptags", **artist_query(artist_id)})
        return parse_top_tags(json.loads(body))

    def similar_artists(self, artist_id: str) -> list[tuple[str, float]]:
        return [(s.artist_id, s.match) for s in self.similar_artists_named(artist_id)]

    def similar_artists_named(self, artist_id: str) -> list[SimilarArtist]:
        import json

        body = self._get({"method": "artist.getsimilar", **artist_query(artist_id)})
        return parse_similar_named(json.loads(body))


# --- Pure parsers with input validation (security: untrusted external data) ---


def _as_list(container: object, key: str) -> list[object]:
    """The list under ``container[key]``, for every shape Last.fm actually sends.

    ``container.get(key, [])`` is not enough, and the difference is a crash. A
    key that is *present and null* — which Last.fm sends for an empty
    collection — makes ``.get`` return ``None``, not the default, and the caller
    then iterates ``None``. A scalar (a stray number or string) iterates as
    characters or raises. Both arrive as ``TypeError`` from inside a parser
    documented as raising ``ValueError`` for a bad payload, out of a call stack
    (``CachedLastfm.similar_artists`` -> ``collaborative_scores``) that nothing
    guards — so one shape change upstream, or one such response already sitting
    in a local cache, takes down ``lavender recommend --user`` with a traceback.

    A single object is wrapped (Last.fm collapses a one-element collection to a
    bare object); anything else that is not a list becomes empty, which is the
    same first-class "no data" every other absence produces here.
    """
    if not isinstance(container, dict):
        return []
    value = container.get(key)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):  # Last.fm returns a bare object for a single item
        return [value]
    return []


def api_error(payload: object) -> Optional[str]:
    """Last.fm's application-level error envelope, if this payload is one.

    Last.fm answers some failures with **HTTP 200** and a body of
    ``{"error": N, "message": "..."}`` — error 29 is "rate limit exceeded". Read
    as data, that body has no ``recenttracks`` key, so the parsers return an
    empty list and the caller cannot tell "your rate limit is exhausted" from
    "this user has no plays". Worse, the raw body was written to the HTTP cache
    before anything looked at it, so the error document was replayed forever.

    Returns a redacted message (never the raw upstream text, which is untrusted
    and could carry anything) or ``None`` when the payload is ordinary data.
    """
    if not isinstance(payload, dict):
        return None
    code = payload.get("error")
    if code is None:
        return None
    return f"Last.fm returned an application error (code {code!r}) with HTTP 200"


def parse_recent_tracks(payload: object) -> list[Scrobble]:
    """Parse user.getrecenttracks JSON, validating shape and skipping 'now playing'."""
    if not isinstance(payload, dict):
        raise ValueError("recent-tracks payload must be an object")
    tracks = _as_list(payload.get("recenttracks", {}), "track")
    out: list[Scrobble] = []
    for t in tracks:
        if not isinstance(t, dict):
            continue
        attr = t.get("@attr", {})
        if isinstance(attr, dict) and attr.get("nowplaying") == "true":
            continue
        date = t.get("date", {})
        ts = date.get("uts") if isinstance(date, dict) else None
        if ts is None:
            continue
        try:
            ts_int = int(ts)
        except (TypeError, ValueError):
            continue  # malformed/non-numeric timestamp — skip, don't crash the batch
        artist = t.get("artist", {})
        if not isinstance(artist, dict):
            continue
        artist_id = str(artist.get("mbid") or artist.get("#text", "")).strip()
        artist_name = str(artist.get("#text", "")).strip()
        if not artist_id and not artist_name:
            continue
        out.append(
            Scrobble(
                artist_id=artist_id,
                artist_name=artist_name,
                track=str(t.get("name", "")).strip(),
                ts=ts_int,
            )
        )
    return out


def parse_top_tags(payload: object, max_tags: int = 10) -> tuple[str, ...]:
    if not isinstance(payload, dict):
        raise ValueError("top-tags payload must be an object")
    tags = _as_list(payload.get("toptags", {}), "tag")
    names: list[str] = []
    for tag in tags:
        if isinstance(tag, dict) and tag.get("name"):
            names.append(str(tag["name"]).strip().lower())
    return tuple(names[:max_tags])


def parse_similar(payload: object) -> list[tuple[str, float]]:
    """The similarity edges alone — what :class:`ScrobbleSource` promises."""
    return [(s.artist_id, s.match) for s in parse_similar_named(payload)]


def parse_similar_named(payload: object) -> list[SimilarArtist]:
    """Similarity edges *with* display names, for candidate discovery.

    ``similar_artists`` keys candidates the same way scrobbles are keyed — MBID
    when there is one, name otherwise — so a discovered candidate's key is not
    something a person can read. The name travels alongside rather than
    replacing the key, because the key is what the catalog and the cache agree
    on and changing it would break that join.
    """
    if not isinstance(payload, dict):
        raise ValueError("similar payload must be an object")
    artists = _as_list(payload.get("similarartists", {}), "artist")
    out: list[SimilarArtist] = []
    for a in artists:
        if not isinstance(a, dict):
            continue
        display = str(a.get("name", "")).strip()
        key = str(a.get("mbid") or display).strip()
        if not key:
            continue
        try:
            match = float(a.get("match", 0.0))
        except (TypeError, ValueError):
            match = 0.0
        out.append(SimilarArtist(key, display or key, max(0.0, min(1.0, match))))
    return out
