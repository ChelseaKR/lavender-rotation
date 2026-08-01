"""The provider-agnostic seam every playlist exporter sits behind.

Before this module, Spotify *was* the export path: its OAuth helpers, its HTTP
transport, and its notion of "export some recommendations" all lived in
``export/spotify.py`` with no separation between the parts that are about OAuth
and the parts that are about Spotify. Adding a second provider by copying that
file would have produced two drifting copies of PKCE, of redirect parsing, and
of the one place in the repo that imports ``requests``.

So the reusable half lives here:

* :class:`Exporter` — the protocol a provider adapter satisfies. It is the
  contract the dashboard and CLI can hold instead of holding "Spotify".
* :class:`PkcePair`, :func:`parse_redirect`, :func:`capture_redirect` — the
  OAuth 2.0 native-app flow (RFC 7636 + a loopback listener). None of this is
  provider-specific; only the URLs are.
* :class:`HttpTransport` / :class:`RequestsTransport` — the injectable HTTP
  surface that keeps every adapter unit-testable with no socket.

**Egress.** ``RequestsTransport`` is the only thing in ``export/`` that opens a
connection, so this module is the single entry in the export half of the
sanctioned-egress allowlist (``tests/test_privacy.py``). That list got *shorter*,
not longer, when a second provider was added — which is the point of putting the
transport here rather than once per adapter. What an exporter is permitted to
send is unchanged and asserted elsewhere: artist and track names only, never
anything from the listening profile and never an identity field.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

from export.models import ExportError, PlaylistExport

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pipeline.models import Recommendation

#: How long a loopback listener waits for the browser to redirect back.
CAPTURE_TIMEOUT = 120.0


@dataclass(frozen=True)
class HttpResponse:
    """A minimal HTTP response: a status code and the parsed JSON body."""

    status: int
    body: dict[str, Any]

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@runtime_checkable
class HttpTransport(Protocol):
    """The tiny HTTP surface a provider client needs. Injectable for testing."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        data: Optional[Mapping[str, str]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
    ) -> HttpResponse: ...


class RequestsTransport:  # pragma: no cover - live network path, verified manually
    """The one live transport in the whole export package.

    Imports ``requests`` lazily, like the Last.fm client, so importing
    ``export`` never pulls a network library into a run that will not use one.
    """

    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        data: Optional[Mapping[str, str]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
    ) -> HttpResponse:
        import requests

        resp = requests.request(
            method,
            url,
            headers=dict(headers or {}),
            data=dict(data) if data is not None else None,
            json=dict(json_body) if json_body is not None else None,
            timeout=self.timeout,
        )
        try:
            body = resp.json() if resp.content else {}
        except ValueError:
            body = {}
        return HttpResponse(status=resp.status_code, body=body if isinstance(body, dict) else {})


@dataclass(frozen=True)
class PkcePair:
    """A PKCE verifier/challenge pair (RFC 7636, S256 method).

    The verifier never leaves process memory: it is generated here, held only
    long enough to be passed to the provider's token exchange, and never
    serialised, logged, or transmitted anywhere except in that POST body
    over TLS.
    """

    verifier: str
    challenge: str

    @classmethod
    def generate(cls) -> PkcePair:
        """Generate a fresh, cryptographically random verifier + S256 challenge."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return cls(verifier=verifier, challenge=challenge)


@runtime_checkable
class Exporter(Protocol):
    """What a provider adapter must offer to be a first-class export target.

    Deliberately narrow. Everything about *how* a provider authenticates —
    which URLs, which scopes, whether it wants a client secret — stays inside
    the adapter, because those differ per provider and pretending otherwise
    would produce a lowest-common-denominator abstraction that fits none of
    them. What callers actually need is stable: a name to show, and a way to
    turn recommendations into a playlist.

    :meth:`export` may raise :class:`~export.models.ExportError`; it must never
    silently drop an artist it could not match. Unmatched names are reported in
    the returned :class:`~export.models.PlaylistExport`.
    """

    #: Stable lowercase identifier, matching ``PlaylistExport.provider``.
    provider: str

    def export(
        self,
        recs: Sequence[Recommendation],
        *,
        username: str = "you",
        playlist_name: Optional[str] = None,
        public: bool = False,
    ) -> PlaylistExport: ...


def parse_redirect(url: str, expected_state: str) -> str:
    """Parse the redirected URL, verify ``state``, and return the ``code``.

    Raises :class:`ExportError` if the provider reported an ``error`` param, or
    if the returned ``state`` does not match ``expected_state`` — the state
    check is what makes CSRF protection an enforced, tested failure path rather
    than a value that is generated but never verified.

    Accepts either a full URL or just the path+query, because
    :func:`capture_redirect` returns the latter.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    error = query.get("error", [""])[0]
    if error:
        raise ExportError(f"authorization failed: {error}")
    returned_state = query.get("state", [""])[0]
    if returned_state != expected_state:
        raise ExportError("OAuth state mismatch — possible CSRF")
    code = query.get("code", [""])[0]
    if not code:
        raise ExportError("redirected URL did not contain an authorization code")
    return code


class _RedirectCaptureHandler(http.server.BaseHTTPRequestHandler):
    """Stashes the redirected path/query on the server, then closes the tab."""

    def do_GET(self) -> None:  # pragma: no cover - stdlib handler naming
        self.server.captured_path = self.path  # type: ignore[attr-defined]
        body = b"<html><body>Authorized. You can close this tab.</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover - silence stdlib
        pass


def loopback_port(redirect_uri: str) -> int:
    """Validate a native-app loopback redirect and return its port."""
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ExportError("redirect URI must use HTTP loopback (127.0.0.1 or localhost)")
    try:
        return parsed.port or 80
    except ValueError as exc:
        raise ExportError("redirect URI has an invalid port") from exc


def capture_redirect(  # pragma: no cover - binds a real socket, verified manually
    redirect_uri: str, timeout: float = CAPTURE_TIMEOUT
) -> str:
    """Run a one-shot loopback listener and return the redirected path+query.

    Binds ``http.server.HTTPServer`` to ``127.0.0.1`` on the redirect URI's port
    and blocks for a single request (or until ``timeout``) — the
    native-app-recommended way to receive an OAuth redirect without the user
    copy-pasting a URL by hand.
    """
    port = loopback_port(redirect_uri)
    server = http.server.HTTPServer(("127.0.0.1", port), _RedirectCaptureHandler)
    server.timeout = timeout
    server.captured_path = None  # type: ignore[attr-defined]
    try:
        server.handle_request()
    finally:
        server.server_close()
    path: Optional[str] = server.captured_path  # type: ignore[attr-defined]
    if not path:
        raise ExportError("timed out waiting for the OAuth redirect")
    return path
