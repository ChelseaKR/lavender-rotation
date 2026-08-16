"""The one HTTP seam the identity enrichers fetch through.

Egress posture (RESPONSIBLE-TECH-AUDITS §C, FIX-07): sanctioned network access
lives in a named, allowlisted module, never scattered across the code that
happens to need a payload. ``pipeline/lastfm.py`` owns the listening-data
transport; this module owns the *identity-source* transport (MusicBrainz,
Wikidata). Both are listed in the egress registry
(``docs/audits/privacy-notes.md``) and in ``tests/test_privacy.py``'s
``NETWORK_ALLOWED``, which is what keeps that registry honest.

Keeping the transport here — rather than importing ``requests`` inside
:mod:`pipeline.enrich` — has a second payoff: every enricher takes its fetcher
as a constructor argument, so the whole parse/resolve path is unit-testable
from recorded payloads with no socket involved.

Three obligations this fetcher discharges on every call:

* **Identify ourselves.** MusicBrainz requires a descriptive ``User-Agent`` with
  contact information and rate-limits (or blocks) clients that omit one.
* **Pace ourselves.** One request per second by default, via the same
  :class:`~pipeline.lastfm.RateLimiter` the Last.fm client uses.
* **Ask once.** Every response is written to the local HTTP cache, so a re-run
  costs nothing upstream. This is a courtesy requirement, not an optimisation.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Optional, Protocol

from pipeline.cache import Cache
from pipeline.lastfm import MAX_ATTEMPTS, RateLimiter, is_transient_failure

log = logging.getLogger("wad.enrich")

#: Sent on every identity-source request. MusicBrainz's rate-limit policy asks
#: for an application name, a version, and a way to contact whoever is running
#: it; an operator supplies the contact half via ``WAD_CONTACT``.
USER_AGENT_BASE = "women-artist-discovery/0.1.0"
PROJECT_URL = "https://github.com/ChelseaKR/women-artist-discovery"

#: MusicBrainz asks for at most one request per second from an anonymous client.
MUSICBRAINZ_MIN_INTERVAL = 1.0


class HttpFetchError(RuntimeError):
    """A failed identity-source fetch, rendered without the request URL.

    These endpoints take no credential — the whole point of preferring
    MusicBrainz and Wikidata is that identity claims are public and citable —
    so there is no secret to leak here the way there is in
    :class:`~pipeline.lastfm.LastfmRequestError`. The message is still built
    from the status and the exception class rather than the URL, so the shape
    of a failure log does not depend on which module raised it.
    """


def build_user_agent(contact: str = "") -> str:
    """The ``User-Agent`` string for identity-source requests.

    ``contact`` is whatever the operator put in ``WAD_CONTACT`` — an email or a
    URL. It is *their* contact detail for an upstream sysadmin to reach, never
    anything derived from the listening data, so it is safe to send and is the
    only way to comply with MusicBrainz's stated policy.
    """
    detail = contact.strip() or PROJECT_URL
    return f"{USER_AGENT_BASE} ( {detail} )"


class Fetcher(Protocol):
    """Fetch a URL's body. The seam every live enricher is built against."""

    def __call__(self, url: str) -> str: ...


class CachedHttpFetcher:
    """Rate-limited, cache-first GET for public identity sources.

    A cache hit performs no network call and does not consume a rate-limit
    slot. ``ttl_days`` is threaded straight through to
    :meth:`~pipeline.cache.Cache.get_cached_response`: ``None`` (the default)
    keeps a stored response forever, and a number makes anything older count as
    a miss — which is how a re-check of upstream identity claims is requested
    (``identity-data-ethics.md``: "recheck per identity-source API change").
    """

    def __init__(
        self,
        cache: Cache,
        *,
        user_agent: str,
        limiter: Optional[RateLimiter] = None,
        ttl_days: Optional[int] = None,
        timeout: float = 15.0,
        now_fn: Callable[[], str] = lambda: time.strftime("%Y-%m-%d"),
    ) -> None:
        if not user_agent.strip():
            raise ValueError("a descriptive User-Agent is required for identity-source requests")
        self.cache = cache
        self.user_agent = user_agent
        self.limiter = limiter or RateLimiter(min_interval=MUSICBRAINZ_MIN_INTERVAL)
        self.ttl_days = ttl_days
        self.timeout = timeout
        self._now = now_fn

    def __call__(self, url: str) -> str:
        cached = self.cache.get_cached_response(url, ttl_days=self.ttl_days)
        if cached is not None:
            return cached
        body = self._get(url)
        self.cache.put_cached_response(url, body, self._now())
        return body

    def _get(self, url: str) -> str:  # pragma: no cover - live network path
        """One request, retried once if it never got an answer.

        Worth more here than on the listening-data path, because the failure is
        *quiet*: a timed-out lookup costs an artist their sourced label and
        leaves them ``unknown``, which is indistinguishable in the output from
        an artist upstream genuinely has no claim about.
        """
        import requests

        for attempt in range(MAX_ATTEMPTS):
            self.limiter.acquire()
            try:
                resp = requests.get(
                    url,
                    headers={"User-Agent": self.user_agent, "Accept": "application/json"},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                exc_name = type(exc).__name__
                if attempt + 1 >= MAX_ATTEMPTS or not is_transient_failure(status, exc_name):
                    detail = f"HTTP {status}" if status is not None else exc_name
                    raise HttpFetchError(f"identity-source request failed ({detail})") from None
                log.warning("stage=enrich_upstream event=retrying")
        raise HttpFetchError(  # pragma: no cover - the loop returns or raises
            "identity-source request failed (RetriesExhausted)"
        )
