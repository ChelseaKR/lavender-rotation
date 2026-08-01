"""TIDAL playlist export via the Authorization Code + PKCE OAuth flow.

The second provider adapter, and the one that made the :mod:`export.base` seam
worth having: everything below is TIDAL-specific — its URLs, its scopes, its
JSON:API response shapes — while PKCE, state verification, the loopback
listener, and the single live HTTP transport are shared with Spotify rather than
copied.

TIDAL is a **public** developer platform (no paid membership, no partner
approval), which is why it is the next adapter rather than Apple Music or
Qobuz. See ``docs/PROJECT-SCOPE.md`` for where those two stand.

To run live you need a TIDAL app and these env vars::

    WAD_TIDAL_CLIENT_ID=...
    WAD_TIDAL_CLIENT_SECRET=...        # optional: public clients use PKCE alone
    WAD_TIDAL_REDIRECT_URI=http://127.0.0.1:8080/callback

OAuth steps the caller drives, identical in shape to the Spotify flow:

1. :meth:`~export.base.PkcePair.generate` → verifier/S256 challenge.
2. :meth:`TidalOAuth.authorize_url` → open it; the user grants the playlist
   scopes; TIDAL redirects back with ``?code=...&state=...``.
3. :func:`~export.base.capture_redirect` → the loopback listener, or paste the
   URL by hand.
4. :func:`~export.base.parse_redirect` → verifies ``state``, returns the code.
5. :meth:`TidalOAuth.exchange_code` → code (+ verifier) for an access token.
6. :func:`export_recommendations` → search each artist, create the playlist,
   add the matched tracks. Unmatched artists are reported, never dropped.

**What is and is not verified here.** Every branch below is exercised offline
against a fake transport, exactly as the Spotify adapter is. The endpoint paths
and JSON:API envelopes come from TIDAL's public developer documentation and have
**not** been exercised against the live service from this repository — nobody
here holds TIDAL app credentials. That is stated plainly rather than implied
away: if a field name has moved, the fix is a constant or a key in this file,
not a redesign, and :class:`TidalClient`'s parsing is written to fail loudly
(:class:`ExportError`) rather than silently return nothing.

**Egress.** Same rule as every other exporter: artist and track names only.
Nothing from the listening profile, no identity field, no scrobble history.
"""

from __future__ import annotations

import base64
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from pipeline.models import Recommendation

from export.base import HttpTransport
from export.models import ExportError, PlaylistExport
from export.tracklist import recommendations_to_tracks

AUTH_URL = "https://login.tidal.com/authorize"
TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"  # noqa: S105 - public endpoint, not a secret
API_ROOT = "https://openapi.tidal.com/v2"
#: Minimal scopes: read who the user is, and write their own playlists.
DEFAULT_SCOPES: tuple[str, ...] = ("user.read", "playlists.write")
#: TIDAL's catalogue is licensed per market, so every catalogue call is scoped
#: to a country. Not a user location signal: it selects a catalogue, and the
#: caller may pass whatever market it wants searched.
DEFAULT_COUNTRY = "US"
#: Conservative batch size for playlist item additions.
_ADD_BATCH = 50


@dataclass(frozen=True)
class TidalCredentials:
    """TIDAL app credentials, read from the environment — never hard-coded.

    ``client_secret`` is optional: a public/native client authenticates the
    token exchange with PKCE alone, and sending an empty secret would be worse
    than sending none.
    """

    client_id: str
    redirect_uri: str
    client_secret: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> TidalCredentials:
        """Build credentials from ``env``; raise :class:`ExportError` if incomplete."""
        client_id = env.get("WAD_TIDAL_CLIENT_ID", "").strip()
        redirect_uri = env.get("WAD_TIDAL_REDIRECT_URI", "").strip()
        client_secret = env.get("WAD_TIDAL_CLIENT_SECRET", "").strip()
        missing = [
            name
            for name, value in (
                ("WAD_TIDAL_CLIENT_ID", client_id),
                ("WAD_TIDAL_REDIRECT_URI", redirect_uri),
            )
            if not value
        ]
        if missing:
            raise ExportError(f"missing TIDAL credentials in env: {', '.join(missing)}")
        return cls(client_id=client_id, redirect_uri=redirect_uri, client_secret=client_secret)

    def token_auth_header(self) -> Optional[str]:
        """Basic auth for the token request, or ``None`` for a public client."""
        if not self.client_secret:
            return None
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return "Basic " + base64.b64encode(raw).decode("ascii")


@dataclass(frozen=True)
class TidalToken:
    """An OAuth access token (plus optional refresh token)."""

    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - OAuth token *type*, not a credential
    scope: str = ""
    expires_in: int = 0
    refresh_token: Optional[str] = None

    @classmethod
    def from_body(cls, body: Mapping[str, Any]) -> TidalToken:
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


class TidalOAuth:
    """Drives the Authorization Code + PKCE flow against TIDAL's endpoints."""

    def __init__(self, credentials: TidalCredentials, transport: HttpTransport) -> None:
        self.credentials = credentials
        self.transport = transport

    def authorize_url(
        self,
        state: str,
        scopes: Sequence[str] = DEFAULT_SCOPES,
        code_challenge: Optional[str] = None,
    ) -> str:
        """The URL to send the user to for consent. Pure; opens no connection."""
        if not state:
            raise ValueError("an opaque 'state' value is required (CSRF protection)")
        params = {
            "client_id": self.credentials.client_id,
            "response_type": "code",
            "redirect_uri": self.credentials.redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, code_verifier: Optional[str] = None) -> TidalToken:
        """Exchange an authorization ``code`` for an access token."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.credentials.redirect_uri,
            "client_id": self.credentials.client_id,
        }
        if code_verifier:
            data["code_verifier"] = code_verifier
        return self._token_request(data)

    def refresh(self, refresh_token: str) -> TidalToken:
        """Refresh an access token. TIDAL may omit a new refresh token; reuse the old."""
        token = self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.credentials.client_id,
            }
        )
        if token.refresh_token is None:
            return TidalToken(
                access_token=token.access_token,
                token_type=token.token_type,
                scope=token.scope,
                expires_in=token.expires_in,
                refresh_token=refresh_token,
            )
        return token

    def _token_request(self, data: Mapping[str, str]) -> TidalToken:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        auth = self.credentials.token_auth_header()
        if auth:
            headers["Authorization"] = auth
        resp = self.transport.request("POST", TOKEN_URL, headers=headers, data=data)
        if not resp.ok:
            raise ExportError(f"TIDAL token request failed (HTTP {resp.status})")
        return TidalToken.from_body(resp.body)


def _resource_id(payload: Any) -> Optional[str]:
    """The ``id`` of a JSON:API resource object, or ``None`` if it is not one."""
    if not isinstance(payload, dict):
        return None
    value = str(payload.get("id", "")).strip()
    return value or None


class TidalClient:
    """Thin, typed wrapper over the TIDAL API endpoints this export uses.

    TIDAL speaks JSON:API, so responses nest the interesting part under ``data``
    and relationships are ``{"id": ..., "type": ...}`` pairs. The helpers below
    unwrap exactly that and nothing more; anything unexpected raises rather than
    degrading into an empty playlist the user would have to notice themselves.
    """

    def __init__(
        self,
        token: TidalToken,
        transport: HttpTransport,
        *,
        country: str = DEFAULT_COUNTRY,
    ) -> None:
        self.token = token
        self.transport = transport
        self.country = country

    def _auth_headers(self, *, json_api: bool = False) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token.access_token}",
            "Accept": "application/vnd.api+json",
        }
        if json_api:
            headers["Content-Type"] = "application/vnd.api+json"
        return headers

    def _call(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
    ) -> Any:
        url = path if path.startswith("http") else f"{API_ROOT}{path}"
        resp = self.transport.request(
            method,
            url,
            headers=self._auth_headers(json_api=json_body is not None),
            json_body=json_body,
        )
        if not resp.ok:
            raise ExportError(f"TIDAL API {method} {path} failed (HTTP {resp.status})")
        return resp.body

    def current_user_id(self) -> str:
        data = self._call("GET", "/users/me").get("data")
        user_id = _resource_id(data)
        if not user_id:
            raise ExportError("could not determine the current TIDAL user id")
        return user_id

    def find_track_id(self, query: str) -> Optional[str]:
        """Search for a track and return the top match's id, or ``None``.

        A miss is a legitimate outcome — the catalogue genuinely may not carry a
        given artist in a given market — so this returns ``None`` and the caller
        reports the artist as unmatched. It is not an error.
        """
        params = urllib.parse.urlencode({"countryCode": self.country, "include": "tracks"})
        encoded = urllib.parse.quote(query, safe="")
        body = self._call("GET", f"/searchResults/{encoded}?{params}")
        included = body.get("included")
        if isinstance(included, list):
            for item in included:
                if isinstance(item, dict) and item.get("type") == "tracks":
                    track_id = _resource_id(item)
                    if track_id:
                        return track_id
        return None

    def create_playlist(self, name: str, description: str = "", public: bool = False) -> str:
        """Create a playlist and return its id.

        ``public`` maps to TIDAL's ``accessType``. The default is PRIVATE: an
        export of someone's listening-derived recommendations is theirs, and a
        tool should not publish it on their behalf without them asking.
        """
        body = self._call(
            "POST",
            "/playlists",
            json_body={
                "data": {
                    "type": "playlists",
                    "attributes": {
                        "name": name,
                        "description": description,
                        "accessType": "PUBLIC" if public else "PRIVATE",
                    },
                }
            },
        )
        playlist_id = _resource_id(body.get("data"))
        if not playlist_id:
            raise ExportError("TIDAL did not return a playlist id")
        return playlist_id

    def add_tracks(self, playlist_id: str, track_ids: Sequence[str]) -> None:
        """Add tracks to a playlist, in conservative batches."""
        quoted = urllib.parse.quote(playlist_id)
        for start in range(0, len(track_ids), _ADD_BATCH):
            batch = list(track_ids[start : start + _ADD_BATCH])
            if not batch:
                continue  # pragma: no cover - range guarantees non-empty batches
            self._call(
                "POST",
                f"/playlists/{quoted}/relationships/items",
                json_body={"data": [{"id": tid, "type": "tracks"} for tid in batch]},
            )

    @staticmethod
    def playlist_url(playlist_id: str) -> str:
        return f"https://tidal.com/playlist/{urllib.parse.quote(playlist_id)}"


def export_recommendations(
    recs: Sequence[Recommendation],
    client: TidalClient,
    *,
    username: str = "you",
    playlist_name: Optional[str] = None,
    public: bool = False,
) -> PlaylistExport:
    """Create a TIDAL playlist from recommendations and report the outcome.

    Same contract as the Spotify path, deliberately: each artist's
    representative track is searched, matches are added, and any artist that did
    not resolve is returned in ``unmatched`` rather than dropped silently. The
    values-aware ordering of ``recs`` is preserved.
    """
    tracks = recommendations_to_tracks(recs)
    if not tracks:
        raise ExportError("nothing to export: the recommendation set is empty")

    name = playlist_name or f"Women-Artist Discovery — {username}"
    description = (
        "Values-aware discovery: women, nonbinary, and sourced female-fronted "
        "artists surfaced from your listening. Identity is sourced, never inferred."
    )

    track_ids: list[str] = []
    unmatched: list[str] = []
    for track in tracks:
        track_id = client.find_track_id(track.query)
        if track_id:
            track_ids.append(track_id)
        else:
            unmatched.append(track.artist_name)

    playlist_id = client.create_playlist(name, description, public=public)
    if track_ids:
        client.add_tracks(playlist_id, track_ids)

    return PlaylistExport(
        provider="tidal",
        playlist_name=name,
        track_count=len(tracks),
        matched_count=len(track_ids),
        playlist_url=TidalClient.playlist_url(playlist_id),
        playlist_id=playlist_id,
        unmatched=tuple(unmatched),
    )


@dataclass(frozen=True)
class TidalExporter:
    """TIDAL as an :class:`~export.base.Exporter`."""

    client: TidalClient
    provider: str = "tidal"

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
