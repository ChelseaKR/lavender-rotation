"""Last.fm scrobble/tag/similarity source, with rate-limit respect + caching.

Two implementations of :class:`ScrobbleSource`:

* :class:`LastfmClient` — the live HTTP client. It honours Last.fm's rate limit
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

import time
import urllib.parse
from collections.abc import Callable, Mapping
from typing import Optional, Protocol, runtime_checkable

from pipeline.cache import Cache
from pipeline.models import Scrobble


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


class LastfmClient:  # pragma: no cover - live network path, verified via integration
    """Live Last.fm client. Network calls are integration-tested, not unit-gated."""

    API_ROOT = "https://ws.audioscrobbler.com/2.0/"

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
        """The cache identity of a request — deliberately *without* the API key.

        The key is a credential, not part of what makes two requests the same
        request: the same method and arguments return the same response whoever
        asks. Including it would have written the secret into the ``http_cache``
        table in clear text, where it would outlive the process, the rotation of
        the key, and any log the operator thought to scrub.

        Changing the key shape invalidates rows an earlier build wrote; that is
        the intended cost. Those rows simply miss and are re-fetched (or aged
        out by ``wad refresh --ttl-days``).
        """
        query = urllib.parse.urlencode({**params, "format": "json"})
        return f"{self.API_ROOT}?{query}"

    def _get(self, params: dict[str, str]) -> str:
        import requests

        key = self.cache_key(params)
        cached = self.cache.get_cached_response(key)
        if cached is not None:
            return cached
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
            body = resp.text
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise LastfmRequestError(
                redacted_failure_message(params.get("method", "?"), status, type(exc).__name__)
            ) from None
        self.cache.put_cached_response(key, body, self._now())
        return body

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
        while page <= total_pages:
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
        out.sort(key=lambda s: s.ts)
        return [s for s in out if s.ts > since_ts]

    def artist_tags(self, artist_id: str) -> tuple[str, ...]:
        import json

        body = self._get({"method": "artist.gettoptags", "mbid": artist_id})
        return parse_top_tags(json.loads(body))

    def similar_artists(self, artist_id: str) -> list[tuple[str, float]]:
        import json

        body = self._get({"method": "artist.getsimilar", "mbid": artist_id})
        return parse_similar(json.loads(body))


# --- Pure parsers with input validation (security: untrusted external data) ---


def parse_recent_tracks(payload: object) -> list[Scrobble]:
    """Parse user.getrecenttracks JSON, validating shape and skipping 'now playing'."""
    if not isinstance(payload, dict):
        raise ValueError("recent-tracks payload must be an object")
    container = payload.get("recenttracks", {})
    tracks = container.get("track", []) if isinstance(container, dict) else []
    if isinstance(tracks, dict):  # Last.fm returns a bare object for a single track
        tracks = [tracks]
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
    container = payload.get("toptags", {})
    tags = container.get("tag", []) if isinstance(container, dict) else []
    if isinstance(tags, dict):
        tags = [tags]
    names: list[str] = []
    for tag in tags:
        if isinstance(tag, dict) and tag.get("name"):
            names.append(str(tag["name"]).strip().lower())
    return tuple(names[:max_tags])


def parse_similar(payload: object) -> list[tuple[str, float]]:
    if not isinstance(payload, dict):
        raise ValueError("similar payload must be an object")
    container = payload.get("similarartists", {})
    artists = container.get("artist", []) if isinstance(container, dict) else []
    if isinstance(artists, dict):
        artists = [artists]
    out: list[tuple[str, float]] = []
    for a in artists:
        if not isinstance(a, dict):
            continue
        key = str(a.get("mbid") or a.get("name", "")).strip()
        if not key:
            continue
        try:
            match = float(a.get("match", 0.0))
        except (TypeError, ValueError):
            match = 0.0
        out.append((key, max(0.0, min(1.0, match))))
    return out
