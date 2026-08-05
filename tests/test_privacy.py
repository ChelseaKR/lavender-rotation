"""Privacy audit §C: no telemetry, and network egress confined to one place.

These are source-level guarantees (DPIA: data-minimisation + purpose-limitation):
the listening data is local-first, so the core must not import analytics SDKs and
must not open network connections anywhere except the explicit Last.fm/enrichment
and Spotify-export client paths.

This is enforcement gate 1 of 2 for FIX-07 (runtime egress guard, see
`docs/audits/privacy-notes.md` "Egress registry / allowlist"): a source-level
scan that catches string-level egress additions in `app/` and `export/` as well
as `pipeline/`/`recommender/`. Gate 2 is the autouse socket-level guard in
`tests/conftest.py`, which catches indirect/transitive runtime egress that a
text scan can't see.
"""

from __future__ import annotations

import socket
from pathlib import Path

import app
import export
import pipeline
import recommender

TELEMETRY_TOKENS = (
    "mixpanel",
    "segment.analytics",
    "amplitude",
    "posthog",
    "sentry_sdk",
    "datadog",
    "google.analytics",
    "googleanalytics",
)

# Network may only be reached from these modules — the live API clients. This
# is the single source of truth for sanctioned egress; keep it in sync with
# "Egress registry / allowlist" in docs/audits/privacy-notes.md.
# `export/base.py` replaced `export/spotify.py` here when the second provider adapter
# landed (#54): the one live HTTP transport moved into the shared seam, so the export
# half of this allowlist got SHORTER as providers were added instead of growing one
# entry per provider. A new adapter that needs its own socket has to change this line.
NETWORK_ALLOWED = {"pipeline/lastfm.py", "pipeline/doctor.py", "export/base.py"}
NETWORK_TOKENS = (
    "import requests",
    "import httpx",
    "import urllib3",
    "import aiohttp",
    "urllib.request",
    "http.client",
    "import socket",
    "webbrowser",
)


def _core_files() -> list[Path]:
    roots = [
        Path(pipeline.__file__).parent,
        Path(recommender.__file__).parent,
        Path(app.__file__).parent,
        Path(export.__file__).parent,
    ]
    return [p for root in roots for p in root.rglob("*.py")]


def _repo_path(path: Path) -> str:
    return path.relative_to(Path(__file__).parents[1]).as_posix()


def test_core_imports_no_telemetry_sdk() -> None:
    for path in _core_files():
        text = path.read_text(encoding="utf-8").lower()
        for token in TELEMETRY_TOKENS:
            assert token not in text, f"{path.name} references telemetry: {token}"


def test_network_access_is_confined_to_api_clients() -> None:
    for path in _core_files():
        if _repo_path(path) in NETWORK_ALLOWED:
            continue
        text = path.read_text(encoding="utf-8")
        for token in NETWORK_TOKENS:
            assert token not in text, f"{path.name} opens network outside an API client: {token}"


def test_runtime_guard_blocks_connection_and_datagram_paths() -> None:
    sock = socket.socket()
    for operation in (
        lambda: sock.connect(("127.0.0.1", 9)),
        lambda: sock.connect_ex(("127.0.0.1", 9)),
        lambda: sock.sendto(b"blocked", ("127.0.0.1", 9)),
        lambda: socket.create_connection(("127.0.0.1", 9)),
    ):
        try:
            operation()
        except RuntimeError as exc:
            assert "egress blocked" in str(exc)
        else:
            raise AssertionError("runtime egress guard did not block a socket path")
    sock.close()


#: Every credential-bearing value object in the repo, paired with the secret it
#: holds. A new provider adapter that adds one belongs on this list — that is the
#: point of keeping the list here rather than one assertion per adapter module.
def _secret_bearing_objects() -> list[tuple[str, object, tuple[str, ...]]]:
    from export.base import PkcePair
    from export.spotify import SpotifyCredentials, SpotifyToken
    from export.tidal import TidalCredentials, TidalToken

    return [
        (
            "SpotifyCredentials",
            SpotifyCredentials(
                client_id="cid",
                client_secret="SPOTIFY-SECRET",  # gitleaks:allow - canary, not a credential
                redirect_uri="http://127.0.0.1/cb",
            ),
            ("SPOTIFY-SECRET",),
        ),
        (
            "SpotifyToken",
            SpotifyToken(access_token="SPOTIFY-ACCESS", refresh_token="SPOTIFY-REFRESH"),
            ("SPOTIFY-ACCESS", "SPOTIFY-REFRESH"),
        ),
        (
            "TidalCredentials",
            TidalCredentials(
                client_id="cid",
                redirect_uri="http://127.0.0.1/cb",
                client_secret="TIDAL-SECRET",  # gitleaks:allow - canary, not a credential
            ),
            ("TIDAL-SECRET",),
        ),
        (
            "TidalToken",
            TidalToken(access_token="TIDAL-ACCESS", refresh_token="TIDAL-REFRESH"),
            ("TIDAL-ACCESS", "TIDAL-REFRESH"),
        ),
        ("PkcePair", PkcePair(verifier="PKCE-VERIFIER", challenge="chal"), ("PKCE-VERIFIER",)),
    ]


def test_credential_objects_never_render_their_secret() -> None:
    """A secret must not survive being rendered as text (CWE-312/CWE-532).

    ``@dataclass``'s generated ``repr`` prints every field by default, so an
    OAuth token object dropped into a log line, an f-string, a debugger frame,
    or a traceback that renders locals would spill the bearer credential in
    clear text — without any call site ever *asking* for the secret. Marking the
    secret fields ``repr=False`` closes that off at the type, so no future call
    site has to remember to.
    """
    for name, obj, secrets_held in _secret_bearing_objects():
        for rendered in (repr(obj), str(obj), f"{obj}"):
            for secret in secrets_held:
                assert secret not in rendered, (
                    f"{name} leaked a secret when rendered as text: {rendered!r}"
                )


def test_credential_reprs_still_carry_their_non_secret_metadata() -> None:
    """The gate above must not be satisfiable by rendering nothing useful."""
    from export.tidal import TidalToken

    rendered = repr(TidalToken(access_token="TIDAL-ACCESS", scope="playlists.write"))
    assert "TidalToken" in rendered
    assert "playlists.write" in rendered


#: The Last.fm key the live client is driven with below. Distinctive enough that
#: finding it anywhere is unambiguous, and never a real credential.
_LASTFM_CANARY = "lastfm-canary-do-not-ship"


def _live_lastfm_client(monkeypatch, responder):
    """A :class:`LastfmClient` whose only network call is ``responder``.

    ``requests.get`` is replaced wholesale, so the real ``_get`` body runs —
    cache lookup, request, status check, cache write — with no socket. Returns
    the client, its cache, and the list that records what was sent.
    """
    import requests
    from pipeline.cache import Cache
    from pipeline.lastfm import LastfmClient, RateLimiter

    sent: list[dict[str, str]] = []

    def fake_get(url, params=None, timeout=None):  # type: ignore[no-untyped-def]
        sent.append(dict(params or {}))
        return responder(url, dict(params or {}))

    monkeypatch.setattr(requests, "get", fake_get)
    cache = Cache(":memory:")
    client = LastfmClient(
        api_key=_LASTFM_CANARY,
        cache=cache,
        limiter=RateLimiter(min_interval=0.0),
        now_fn=lambda: "2026-08-01",
    )
    return client, cache, sent


def test_lastfm_api_key_never_reaches_the_local_cache(monkeypatch) -> None:
    """The key authenticates a request; it is not part of the request's identity.

    Last.fm has no header auth, so the credential rides in the query string —
    and the query string used to *be* the ``http_cache`` key, which wrote the
    key into the operator's SQLite file in clear text, outliving the process and
    any rotation of the key. The cache key is now built without it.
    """

    class _Resp:
        status_code = 200
        text = '{"artist": {"tags": {"tag": []}}}'

        def raise_for_status(self) -> None:
            return None

    client, cache, sent = _live_lastfm_client(monkeypatch, lambda url, params: _Resp())
    client.artist_tags("mitski")

    rows = cache.conn.execute("SELECT url, body FROM http_cache").fetchall()
    assert rows, "expected the live client to have written a cache row"
    for url, body in rows:
        assert _LASTFM_CANARY not in url, f"API key persisted into the cache key: {url!r}"
        assert _LASTFM_CANARY not in body
    # …and the gate is not passing merely because the key was never sent.
    assert sent and sent[0]["api_key"] == _LASTFM_CANARY
    cache.close()


def test_lastfm_api_key_never_reaches_an_exception_message(monkeypatch) -> None:
    """``requests`` puts the full URL — key included — in every error it raises.

    ``raise_for_status`` renders "… for url: https://…&api_key=…", and a
    connection failure renders "… Max retries exceeded with url: /2.0/?…". Either
    one escaping would put the credential into every traceback and crash report
    downstream, so the client re-raises its own error ``from None``. The whole
    formatted traceback is checked, not just the message, because ``from None``
    is what stops the leaking original being chained into it.
    """
    import traceback

    import requests
    from pipeline.lastfm import LastfmRequestError

    leaky_url = (
        "https://ws.audioscrobbler.com/2.0/?method=artist.gettoptags"
        f"&api_key={_LASTFM_CANARY}&format=json"
    )
    # What `raise_for_status` actually raises: a message carrying the URL, and a
    # response to read the status off. The other three carry only the message.
    forbidden = requests.HTTPError(f"403 Client Error: Forbidden for url: {leaky_url}")
    forbidden.response = requests.Response()
    forbidden.response.status_code = 403

    failures = (
        (forbidden, "HTTP 403"),
        (requests.HTTPError(f"500 Server Error for url: {leaky_url}"), "HTTPError"),
        (
            requests.ConnectionError(f"Max retries exceeded with url: {leaky_url}"),
            "ConnectionError",
        ),
        (requests.Timeout(f"Read timed out for url: {leaky_url}"), "Timeout"),
    )
    for failure, expected_detail in failures:
        assert _LASTFM_CANARY in str(failure), "the canary failure must actually carry the key"

        def responder(url, params, failure=failure):  # type: ignore[no-untyped-def]
            raise failure

        client, cache, _ = _live_lastfm_client(monkeypatch, responder)
        try:
            client.artist_tags("mitski")
        except LastfmRequestError as exc:
            rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            assert _LASTFM_CANARY not in rendered, (
                f"API key survived into a rendered traceback: {rendered!r}"
            )
            # …and what is left is still enough to diagnose the failure with.
            assert "artist.gettoptags" in str(exc)
            assert expected_detail in str(exc)
        else:
            raise AssertionError(f"{type(failure).__name__} did not surface as LastfmRequestError")
        finally:
            cache.close()


def test_cache_uses_only_stdlib_sqlite() -> None:
    cache_src = (Path(pipeline.__file__).parent / "cache.py").read_text(encoding="utf-8")
    assert "import sqlite3" in cache_src
    for token in ("requests", "boto3", "psycopg", "pymongo", "redis"):
        assert token not in cache_src
