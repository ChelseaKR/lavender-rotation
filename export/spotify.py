"""Spotify playlist export via the Authorization Code OAuth flow.

Design mirrors :mod:`pipeline.lastfm`: the network is reached through a small
injectable :class:`HttpTransport`, so the whole flow is unit-tested offline with
a fake transport, and the one live implementation (:class:`RequestsTransport`)
is the only thing that actually opens a socket. Credentials come from the
environment only — there are no defaults and nothing is ever hard-coded.

To run live you need a Spotify app (client id + secret) and these env vars::

    WAD_SPOTIFY_CLIENT_ID=...
    WAD_SPOTIFY_CLIENT_SECRET=...
    WAD_SPOTIFY_REDIRECT_URI=http://127.0.0.1:8080/callback

OAuth steps the caller drives (native-app hardened: PKCE + state, a loopback
listener as the primary redirect capture):
1. :meth:`PkcePair.generate` → a fresh verifier/S256-challenge pair; the
   verifier never leaves process memory.
2. :meth:`SpotifyOAuth.authorize_url` → open it (passing the challenge and an
   opaque ``state``); the user grants the playlist scopes; Spotify redirects
   back to ``redirect_uri`` with ``?code=...&state=...``.
3. :func:`capture_redirect` → a tiny stdlib loopback HTTP server bound to
   ``127.0.0.1`` on the redirect URI's port, waiting for that one request; or,
   as a fallback, the caller pastes the full redirected URL.
4. :func:`parse_redirect` → extracts ``code``, verifies ``state`` matches
   (raising :class:`ExportError` — "possible CSRF" — on mismatch) and raises
   on any Spotify ``error`` param.
5. :meth:`SpotifyOAuth.exchange_code` → swap the code (+ PKCE verifier) for an
   access token.
6. :func:`export_recommendations` → search each artist, create the playlist,
   add the matched tracks. Unmatched artists are reported, never dropped silently.

The OAuth machinery (PKCE, state verification, the loopback listener) and the
one live HTTP transport now live in :mod:`export.base`, shared with every other
provider adapter; only the URLs, scopes, and API shapes below are Spotify's.
:class:`SpotifyExporter` is this module's :class:`~export.base.Exporter`.
"""

from __future__ import annotations

import base64
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from pipeline.models import Recommendation

from export.base import (
    HttpResponse,
    HttpTransport,
    PkcePair,
    RequestsTransport,
    capture_redirect,
    parse_redirect,
)
from export.models import ExportError, PlaylistExport
from export.tracklist import recommendations_to_tracks

__all__ = [
    "API_ROOT",
    "AUTH_URL",
    "DEFAULT_SCOPES",
    "TOKEN_URL",
    "HttpResponse",
    "HttpTransport",
    "PkcePair",
    "RequestsTransport",
    "SpotifyClient",
    "SpotifyCredentials",
    "SpotifyExporter",
    "SpotifyOAuth",
    "SpotifyToken",
    "capture_redirect",
    "export_recommendations",
    "parse_redirect",
]

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"  # noqa: S105 - public endpoint, not a secret
API_ROOT = "https://api.spotify.com/v1"
#: Minimal scopes: create/modify the user's own playlists, nothing more.
DEFAULT_SCOPES: tuple[str, ...] = ("playlist-modify-private", "playlist-modify-public")
#: Spotify caps additions at 100 URIs per request.
_ADD_BATCH = 100


@dataclass(frozen=True)
class SpotifyCredentials:
    """Spotify app credentials, read from the environment — never hard-coded.

    ``client_secret`` is ``repr=False`` so the dataclass's generated ``repr``
    cannot spill it into a traceback, a debugger frame, or a log line that
    renders this object.
    """

    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> SpotifyCredentials:
        """Build credentials from ``env``; raise :class:`ExportError` if incomplete."""
        client_id = env.get("WAD_SPOTIFY_CLIENT_ID", "").strip()
        client_secret = env.get("WAD_SPOTIFY_CLIENT_SECRET", "").strip()
        redirect_uri = env.get("WAD_SPOTIFY_REDIRECT_URI", "").strip()
        missing = [
            name
            for name, value in (
                ("WAD_SPOTIFY_CLIENT_ID", client_id),
                ("WAD_SPOTIFY_CLIENT_SECRET", client_secret),
                ("WAD_SPOTIFY_REDIRECT_URI", redirect_uri),
            )
            if not value
        ]
        if missing:
            raise ExportError(f"missing Spotify credentials in env: {', '.join(missing)}")
        return cls(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)

    def _basic_auth_header(self) -> str:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")


@dataclass(frozen=True)
class SpotifyToken:
    """An OAuth access token (plus optional refresh token).

    Both token fields are ``repr=False``: rendering this object anywhere —
    ``print``, a log record, an unhandled traceback that shows locals — must
    never emit the bearer credential in clear text. Only the non-secret
    metadata (type, scope, lifetime) survives into the ``repr``.
    """

    access_token: str = field(repr=False)
    token_type: str = "Bearer"  # noqa: S105 - OAuth token *type*, not a credential
    scope: str = ""
    expires_in: int = 0
    refresh_token: Optional[str] = field(default=None, repr=False)

    @classmethod
    def from_body(cls, body: Mapping[str, Any]) -> SpotifyToken:
        access = str(body.get("access_token", "")).strip()
        if not access:
            raise ExportError("token response did not contain an access_token")
        return cls(
            access_token=access,
            token_type=str(body.get("token_type", "Bearer")),
            scope=str(body.get("scope", "")),
            expires_in=int(body.get("expires_in", 0) or 0),
            refresh_token=(str(body["refresh_token"]) if body.get("refresh_token") else None),
        )


class SpotifyOAuth:
    """Drives the Authorization Code flow against the documented Spotify endpoints."""

    def __init__(self, credentials: SpotifyCredentials, transport: HttpTransport) -> None:
        self.credentials = credentials
        self.transport = transport

    def authorize_url(
        self,
        state: str,
        scopes: Sequence[str] = DEFAULT_SCOPES,
        show_dialog: bool = False,
        code_challenge: Optional[str] = None,
    ) -> str:
        """The URL to send the user to for consent. Pure; opens no connection.

        ``code_challenge`` should be a :class:`PkcePair`'s ``challenge``; when
        given, it is sent with ``code_challenge_method=S256`` (RFC 7636),
        hardening the flow against authorization-code interception.
        """
        if not state:
            raise ValueError("an opaque 'state' value is required (CSRF protection)")
        params = {
            "client_id": self.credentials.client_id,
            "response_type": "code",
            "redirect_uri": self.credentials.redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "show_dialog": "true" if show_dialog else "false",
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, code_verifier: Optional[str] = None) -> SpotifyToken:
        """Exchange an authorization ``code`` for an access token.

        ``code_verifier`` should be the :class:`PkcePair` verifier matching the
        challenge sent to :meth:`authorize_url`; when given, it is included in
        the token-request body (PKCE token exchange, RFC 7636 §4.5).
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.credentials.redirect_uri,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier
        return self._token_request(data)

    def refresh(self, refresh_token: str) -> SpotifyToken:
        """Refresh an access token. Spotify may omit a new refresh token; reuse the old."""
        token = self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
        if token.refresh_token is None:
            return SpotifyToken(
                access_token=token.access_token,
                token_type=token.token_type,
                scope=token.scope,
                expires_in=token.expires_in,
                refresh_token=refresh_token,
            )
        return token

    def _token_request(self, data: Mapping[str, str]) -> SpotifyToken:
        resp = self.transport.request(
            "POST",
            TOKEN_URL,
            headers={
                "Authorization": self.credentials._basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data=data,
        )
        if not resp.ok:
            raise ExportError(f"Spotify token request failed (HTTP {resp.status})")
        return SpotifyToken.from_body(resp.body)


class SpotifyClient:
    """Thin, typed wrapper over the Spotify Web API endpoints we use."""

    def __init__(self, token: SpotifyToken, transport: HttpTransport) -> None:
        self.token = token
        self.transport = transport

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token.access_token}"}

    def _call(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
    ) -> HttpResponse:
        url = path if path.startswith("http") else f"{API_ROOT}{path}"
        resp = self.transport.request(
            method, url, headers=self._auth_headers(), json_body=json_body
        )
        if not resp.ok:
            raise ExportError(f"Spotify API {method} {path} failed (HTTP {resp.status})")
        return resp

    def current_user_id(self) -> str:
        body = self._call("GET", "/me").body
        user_id = str(body.get("id", "")).strip()
        if not user_id:
            raise ExportError("could not determine the current Spotify user id")
        return user_id

    def find_track_uri(self, query: str) -> Optional[str]:
        """Search for a track and return the top match's URI, or ``None``."""
        q = urllib.parse.urlencode({"q": query, "type": "track", "limit": "1"})
        body = self._call("GET", f"/search?{q}").body
        items = body.get("tracks", {})
        track_list = items.get("items", []) if isinstance(items, dict) else []
        if not isinstance(track_list, list) or not track_list:
            return None
        first = track_list[0]
        uri = str(first.get("uri", "")).strip() if isinstance(first, dict) else ""
        return uri or None

    def create_playlist(
        self, user_id: str, name: str, description: str = "", public: bool = False
    ) -> tuple[str, Optional[str]]:
        """Create a playlist; return its id and (if present) its web URL."""
        body = self._call(
            "POST",
            f"/users/{urllib.parse.quote(user_id)}/playlists",
            json_body={"name": name, "description": description, "public": public},
        ).body
        playlist_id = str(body.get("id", "")).strip()
        if not playlist_id:
            raise ExportError("Spotify did not return a playlist id")
        external = body.get("external_urls", {})
        url = external.get("spotify") if isinstance(external, dict) else None
        return playlist_id, (str(url) if url else None)

    def add_tracks(self, playlist_id: str, uris: Sequence[str]) -> None:
        """Add track URIs to a playlist, batching within Spotify's 100-per-call cap."""
        for start in range(0, len(uris), _ADD_BATCH):
            batch = list(uris[start : start + _ADD_BATCH])
            if not batch:
                continue  # pragma: no cover - range guarantees non-empty batches
            self._call(
                "POST",
                f"/playlists/{urllib.parse.quote(playlist_id)}/tracks",
                json_body={"uris": batch},
            )


def export_recommendations(
    recs: Sequence[Recommendation],
    client: SpotifyClient,
    *,
    username: str = "you",
    playlist_name: Optional[str] = None,
    public: bool = False,
) -> PlaylistExport:
    """Create a Spotify playlist from recommendations and report the outcome.

    Each artist's representative track is searched; matches are added, and any
    artist that did not resolve to a track is returned in ``unmatched`` — never
    dropped silently. The values-aware ordering of ``recs`` is preserved.
    """
    tracks = recommendations_to_tracks(recs)
    if not tracks:
        raise ExportError("nothing to export: the recommendation set is empty")

    name = playlist_name or f"Women-Artist Discovery — {username}"
    description = (
        "Values-aware discovery: women, nonbinary, and sourced female-fronted "
        "artists surfaced from your listening. Identity is sourced, never inferred."
    )

    uris: list[str] = []
    unmatched: list[str] = []
    for track in tracks:
        uri = client.find_track_uri(track.query)
        if uri:
            uris.append(uri)
        else:
            unmatched.append(track.artist_name)

    user_id = client.current_user_id()
    playlist_id, url = client.create_playlist(user_id, name, description, public=public)
    if uris:
        client.add_tracks(playlist_id, uris)

    return PlaylistExport(
        provider="spotify",
        playlist_name=name,
        track_count=len(tracks),
        matched_count=len(uris),
        playlist_url=url,
        playlist_id=playlist_id,
        unmatched=tuple(unmatched),
    )


@dataclass(frozen=True)
class SpotifyExporter:
    """Spotify as an :class:`~export.base.Exporter`.

    A thin adapter so callers can hold the protocol rather than the module. The
    authenticated :class:`SpotifyClient` is constructed by whoever drove the
    OAuth flow, because consent is a user action and cannot be hidden inside a
    constructor.
    """

    client: SpotifyClient
    provider: str = "spotify"

    def export(
        self,
        recs: Sequence[Recommendation],
        *,
        username: str = "you",
        playlist_name: Optional[str] = None,
        public: bool = False,
    ) -> PlaylistExport:
        return export_recommendations(
            recs,
            self.client,
            username=username,
            playlist_name=playlist_name,
            public=public,
        )
