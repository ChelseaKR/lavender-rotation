"""Playlist export: fallback formats are portable; the Spotify flow is sourced-clean.

The live network is never touched — a :class:`FakeTransport` stands in for HTTP,
exactly as :class:`pipeline.lastfm.FixtureLastfm` stands in for Last.fm. What
*does* need real credentials (a Spotify app + a browser OAuth consent) is called
out in the module docstring and cannot run here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
from collections.abc import Mapping
from typing import Any, Optional

import pytest
from export import spotify as sp
from export.base import Exporter, loopback_port
from export.models import ExportError, ExportFormat, PlaylistExport, PlaylistTrack
from export.spotify import (
    HttpResponse,
    HttpTransport,
    PkcePair,
    SpotifyClient,
    SpotifyCredentials,
    SpotifyExporter,
    SpotifyOAuth,
    SpotifyToken,
    export_recommendations,
    parse_redirect,
)
from export.tracklist import (
    recommendations_to_tracks,
    render,
    to_csv,
    to_jspf,
    to_m3u,
    to_plaintext,
)
from recommender.hybrid import recommend

_ENV = {
    "WAD_SPOTIFY_CLIENT_ID": "cid",
    "WAD_SPOTIFY_CLIENT_SECRET": "secret",  # dummy fixture secret
    "WAD_SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8080/callback",
}


class FakeTransport:
    """An in-memory HTTP double routing the handful of Spotify endpoints we use."""

    def __init__(self, missing_artists: tuple[str, ...] = ()) -> None:
        self.calls: list[dict[str, Any]] = []
        self.missing = missing_artists

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        data: Optional[Mapping[str, str]] = None,
        json_body: Optional[Mapping[str, Any]] = None,
    ) -> HttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "data": dict(data) if data else None,
                "json": dict(json_body) if json_body else None,
            }
        )
        if url == sp.TOKEN_URL:
            return HttpResponse(
                200,
                {
                    "access_token": "access-123",
                    "token_type": "Bearer",
                    "scope": "playlist-modify-private",
                    "expires_in": 3600,
                    "refresh_token": "refresh-123",
                },
            )
        if url.endswith("/me"):
            return HttpResponse(200, {"id": "user42"})
        if "/search" in url:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("q", [""])[0]
            if any(name.lower() in query.lower() for name in self.missing):
                return HttpResponse(200, {"tracks": {"items": []}})
            slug = urllib.parse.quote(query)
            return HttpResponse(200, {"tracks": {"items": [{"uri": f"spotify:track:{slug}"}]}})
        if method == "POST" and "/users/" in url and url.endswith("/playlists"):
            return HttpResponse(
                201,
                {
                    "id": "playlist99",
                    "external_urls": {"spotify": "https://open.spotify.com/playlist/playlist99"},
                },
            )
        if method == "POST" and url.endswith("/tracks"):
            return HttpResponse(201, {"snapshot_id": "snap-1"})
        return HttpResponse(404, {})  # pragma: no cover - defensive default


# --- Fallback formats (no account, no network) -------------------------------


def test_recommendations_to_tracks_preserves_order_and_carries_why(
    profile, catalog, source
) -> None:
    recs = recommend(profile, catalog, source, k=5, lens_strength=0.5)
    tracks = recommendations_to_tracks(recs)
    assert [t.artist_name for t in tracks] == [r.artist.name for r in recs]
    assert all(t.query for t in tracks)
    assert all(t.why for t in tracks)  # transparency carried into the export
    assert not tracks[0].resolved  # offline: nothing matched to a provider yet


def test_plaintext_is_numbered_and_human_readable(profile, catalog, source) -> None:
    recs = recommend(profile, catalog, source, k=3, lens_strength=0.5)
    text = to_plaintext(recommendations_to_tracks(recs))
    lines = text.splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("1. ")
    assert recs[0].artist.name in lines[0]


def test_csv_has_header_and_a_row_per_track(profile, catalog, source) -> None:
    recs = recommend(profile, catalog, source, k=4, lens_strength=0.5)
    csv_text = to_csv(recommendations_to_tracks(recs))
    rows = [r for r in csv_text.splitlines() if r]
    assert rows[0] == "rank,artist,artist_id,search_query,why"
    assert len(rows) == 5  # header + 4


def test_m3u_is_valid_extm3u(profile, catalog, source) -> None:
    recs = recommend(profile, catalog, source, k=2, lens_strength=0.5)
    m3u = to_m3u(recommendations_to_tracks(recs), playlist_name="Mix")
    assert m3u.startswith("#EXTM3U")
    assert "#PLAYLIST:Mix" in m3u
    assert m3u.count("#EXTINF:-1,") == 2


def test_jspf_is_valid_json_playlist(profile, catalog, source) -> None:
    recs = recommend(profile, catalog, source, k=3, lens_strength=0.5)
    doc = json.loads(to_jspf(recommendations_to_tracks(recs), playlist_name="Mix"))
    assert doc["playlist"]["title"] == "Mix"
    assert len(doc["playlist"]["track"]) == 3
    assert doc["playlist"]["track"][0]["creator"] == recs[0].artist.name


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_render_dispatches_every_format(profile, catalog, source, fmt) -> None:
    recs = recommend(profile, catalog, source, k=2, lens_strength=0.5)
    out = render(recommendations_to_tracks(recs), fmt)
    assert out.strip()


# --- Spotify credentials + OAuth ---------------------------------------------


def test_credentials_from_env_reads_all_three() -> None:
    creds = SpotifyCredentials.from_env(_ENV)
    assert creds.client_id == "cid"
    assert creds.redirect_uri.endswith("/callback")


def test_credentials_from_env_reports_every_missing_var() -> None:
    with pytest.raises(ExportError) as exc:
        SpotifyCredentials.from_env({"WAD_SPOTIFY_CLIENT_ID": "cid"})
    msg = str(exc.value)
    assert "WAD_SPOTIFY_CLIENT_SECRET" in msg
    assert "WAD_SPOTIFY_REDIRECT_URI" in msg
    assert "WAD_SPOTIFY_CLIENT_ID" not in msg  # this one was supplied


def test_authorize_url_is_well_formed_and_scoped() -> None:
    oauth = SpotifyOAuth(SpotifyCredentials.from_env(_ENV), FakeTransport())
    url = oauth.authorize_url(state="xyz", show_dialog=True)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith(sp.AUTH_URL)
    assert qs["response_type"] == ["code"]
    assert qs["state"] == ["xyz"]
    assert qs["client_id"] == ["cid"]
    assert "playlist-modify-private" in qs["scope"][0]
    assert qs["show_dialog"] == ["true"]


def test_authorize_url_requires_state() -> None:
    oauth = SpotifyOAuth(SpotifyCredentials.from_env(_ENV), FakeTransport())
    with pytest.raises(ValueError, match="state"):
        oauth.authorize_url(state="")


def test_exchange_code_sends_basic_auth_and_returns_token() -> None:
    transport = FakeTransport()
    oauth = SpotifyOAuth(SpotifyCredentials.from_env(_ENV), transport)
    token = oauth.exchange_code("auth-code")
    assert isinstance(token, SpotifyToken)
    assert token.access_token == "access-123"
    assert token.refresh_token == "refresh-123"
    sent = transport.calls[0]
    assert sent["url"] == sp.TOKEN_URL
    assert sent["headers"]["Authorization"].startswith("Basic ")
    assert sent["data"]["grant_type"] == "authorization_code"
    assert sent["data"]["code"] == "auth-code"


# --- PKCE + loopback OAuth hardening (FIX-08) ---------------------------------


def test_pkce_challenge_is_a_valid_s256_of_the_verifier() -> None:
    pair = PkcePair.generate()
    expected = base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode()).digest())
    expected = expected.rstrip(b"=").decode("ascii")
    assert pair.challenge == expected
    assert len(pair.verifier) >= 43  # RFC 7636 minimum verifier entropy
    assert "=" not in pair.challenge  # unpadded, URL-safe


def test_pkce_generate_produces_fresh_pairs_each_call() -> None:
    a, b = PkcePair.generate(), PkcePair.generate()
    assert a.verifier != b.verifier
    assert a.challenge != b.challenge


def test_authorize_url_includes_code_challenge_method_s256() -> None:
    oauth = SpotifyOAuth(SpotifyCredentials.from_env(_ENV), FakeTransport())
    pair = PkcePair.generate()
    url = oauth.authorize_url(state="xyz", code_challenge=pair.challenge)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert qs["code_challenge"] == [pair.challenge]
    assert qs["code_challenge_method"] == ["S256"]


def test_authorize_url_omits_pkce_params_when_no_challenge_given() -> None:
    oauth = SpotifyOAuth(SpotifyCredentials.from_env(_ENV), FakeTransport())
    url = oauth.authorize_url(state="xyz")
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert "code_challenge" not in qs
    assert "code_challenge_method" not in qs


def test_exchange_code_sends_code_verifier_in_the_form_body() -> None:
    transport = FakeTransport()
    oauth = SpotifyOAuth(SpotifyCredentials.from_env(_ENV), transport)
    oauth.exchange_code("auth-code", code_verifier="verifier-abc")
    sent = transport.calls[0]
    assert sent["data"]["code_verifier"] == "verifier-abc"


def test_exchange_code_omits_verifier_when_not_given() -> None:
    transport = FakeTransport()
    oauth = SpotifyOAuth(SpotifyCredentials.from_env(_ENV), transport)
    oauth.exchange_code("auth-code")
    sent = transport.calls[0]
    assert "code_verifier" not in sent["data"]


def test_parse_redirect_returns_code_on_matching_state() -> None:
    url = "http://127.0.0.1:8080/callback?code=abc123&state=xyz"
    assert parse_redirect(url, expected_state="xyz") == "abc123"


def test_parse_redirect_works_on_path_and_query_only() -> None:
    # capture_redirect returns just the path+query (no scheme/host); parse_redirect
    # must handle that the same as a full URL.
    assert parse_redirect("/callback?code=abc&state=s", expected_state="s") == "abc"


def test_parse_redirect_raises_on_state_mismatch() -> None:
    url = "http://127.0.0.1:8080/callback?code=abc123&state=wrong"
    with pytest.raises(ExportError, match="state mismatch"):
        parse_redirect(url, expected_state="xyz")


def test_parse_redirect_raises_on_spotify_error_param() -> None:
    url = "http://127.0.0.1:8080/callback?error=access_denied&state=xyz"
    with pytest.raises(ExportError, match="access_denied"):
        parse_redirect(url, expected_state="xyz")


def test_parse_redirect_raises_when_code_missing() -> None:
    url = "http://127.0.0.1:8080/callback?state=xyz"
    with pytest.raises(ExportError, match="authorization code"):
        parse_redirect(url, expected_state="xyz")


@pytest.mark.parametrize(
    "uri",
    [
        "https://127.0.0.1:8080/callback",
        "http://0.0.0.0:8080/callback",
        "http://192.168.1.10:8080/callback",
    ],
)
def test_loopback_listener_rejects_non_loopback_or_https_uris(uri: str) -> None:
    # Moved to export.base with the rest of the provider-agnostic OAuth flow (#54);
    # the guarantee is unchanged and now covers every adapter, not just Spotify.
    with pytest.raises(ExportError, match="HTTP loopback"):
        loopback_port(uri)


def test_loopback_listener_accepts_localhost_and_returns_port() -> None:
    assert loopback_port("http://localhost:8765/callback") == 8765


def test_refresh_reuses_old_refresh_token_when_absent() -> None:
    class NoRefresh(FakeTransport):
        def request(self, method, url, **kw):  # type: ignore[override]
            resp = super().request(method, url, **kw)
            if url == sp.TOKEN_URL:
                body = dict(resp.body)
                body.pop("refresh_token", None)
                return HttpResponse(resp.status, body)
            return resp

    oauth = SpotifyOAuth(SpotifyCredentials.from_env(_ENV), NoRefresh())
    token = oauth.refresh("old-refresh")
    assert token.refresh_token == "old-refresh"  # carried over, not lost


def test_token_request_raises_on_http_error() -> None:
    class Failing(FakeTransport):
        def request(self, method, url, **kw):  # type: ignore[override]
            return HttpResponse(400, {"error": "invalid_grant"})

    oauth = SpotifyOAuth(SpotifyCredentials.from_env(_ENV), Failing())
    with pytest.raises(ExportError, match="token request failed"):
        oauth.exchange_code("bad")


def test_token_from_body_requires_access_token() -> None:
    with pytest.raises(ExportError, match="access_token"):
        SpotifyToken.from_body({"token_type": "Bearer"})


def test_refresh_keeps_a_newly_issued_refresh_token() -> None:
    # The default FakeTransport returns a fresh refresh token; it must be kept.
    oauth = SpotifyOAuth(SpotifyCredentials.from_env(_ENV), FakeTransport())
    token = oauth.refresh("old-refresh")
    assert token.refresh_token == "refresh-123"


# --- Spotify client ----------------------------------------------------------


def _client(transport: HttpTransport) -> SpotifyClient:
    return SpotifyClient(SpotifyToken(access_token="access-123"), transport)


def test_client_is_an_http_transport_consumer() -> None:
    assert isinstance(FakeTransport(), HttpTransport)  # runtime_checkable Protocol


def test_current_user_id() -> None:
    assert _client(FakeTransport()).current_user_id() == "user42"


def test_find_track_uri_match_and_miss() -> None:
    client = _client(FakeTransport(missing_artists=("Nobody Band",)))
    assert client.find_track_uri('artist:"Soccer Mommy"') is not None
    assert client.find_track_uri('artist:"Nobody Band"') is None


def test_create_playlist_returns_id_and_url() -> None:
    pid, url = _client(FakeTransport()).create_playlist("user42", "My Mix")
    assert pid == "playlist99"
    assert url == "https://open.spotify.com/playlist/playlist99"


def test_add_tracks_batches_within_the_100_cap() -> None:
    transport = FakeTransport()
    client = _client(transport)
    client.add_tracks("playlist99", [f"spotify:track:{i}" for i in range(150)])
    track_posts = [c for c in transport.calls if c["url"].endswith("/tracks")]
    assert len(track_posts) == 2  # 100 + 50
    assert len(track_posts[0]["json"]["uris"]) == 100
    assert len(track_posts[1]["json"]["uris"]) == 50


def test_api_error_raises_export_error() -> None:
    class Failing(FakeTransport):
        def request(self, method, url, **kw):  # type: ignore[override]
            if url.endswith("/me"):
                return HttpResponse(401, {"error": "expired"})
            return super().request(method, url, **kw)

    with pytest.raises(ExportError, match="failed"):
        _client(Failing()).current_user_id()


def test_current_user_id_raises_when_blank() -> None:
    class Blank(FakeTransport):
        def request(self, method, url, **kw):  # type: ignore[override]
            if url.endswith("/me"):
                return HttpResponse(200, {"id": ""})
            return super().request(method, url, **kw)

    with pytest.raises(ExportError, match="user id"):
        _client(Blank()).current_user_id()


# --- End-to-end export -------------------------------------------------------


def test_export_recommendations_creates_playlist_and_reports_unmatched(
    profile, catalog, source
) -> None:
    recs = recommend(profile, catalog, source, k=6, lens_strength=0.5)
    # Force one artist to be unmatched to prove it is reported, never dropped.
    missing_name = recs[0].artist.name
    transport = FakeTransport(missing_artists=(missing_name,))
    client = _client(transport)

    result = export_recommendations(recs, client, username="demo")

    assert isinstance(result, PlaylistExport)
    assert result.provider == "spotify"
    assert result.track_count == len(recs)
    assert result.matched_count == len(recs) - 1
    assert missing_name in result.unmatched
    assert result.playlist_url and result.playlist_id == "playlist99"
    assert not result.fully_matched
    # Order preserved + a create + at least one add happened.
    assert any(c["url"].endswith("/playlists") for c in transport.calls)
    assert any(c["url"].endswith("/tracks") for c in transport.calls)


def test_export_default_playlist_name_includes_username(profile, catalog, source) -> None:
    recs = recommend(profile, catalog, source, k=3, lens_strength=0.5)
    result = export_recommendations(recs, _client(FakeTransport()), username="chelsea")
    assert "chelsea" in result.playlist_name
    assert result.fully_matched


def test_create_playlist_blank_id_raises() -> None:
    class Blank(FakeTransport):
        def request(self, method, url, **kw):  # type: ignore[override]
            if method == "POST" and url.endswith("/playlists"):
                return HttpResponse(201, {"id": ""})
            return super().request(method, url, **kw)

    with pytest.raises(ExportError, match="playlist id"):
        _client(Blank()).create_playlist("user42", "Mix")


def test_export_with_no_matches_creates_empty_playlist_and_skips_add(
    profile, catalog, source
) -> None:
    recs = recommend(profile, catalog, source, k=4, lens_strength=0.5)
    all_names = tuple(r.artist.name for r in recs)
    transport = FakeTransport(missing_artists=all_names)
    result = export_recommendations(recs, _client(transport), username="demo")
    assert result.matched_count == 0
    assert set(result.unmatched) == set(all_names)
    assert not any(c["url"].endswith("/tracks") for c in transport.calls)  # nothing to add


def test_export_empty_recommendations_raises() -> None:
    with pytest.raises(ExportError, match="empty"):
        export_recommendations([], _client(FakeTransport()))


def test_playlist_track_and_export_value_helpers() -> None:
    t = PlaylistTrack(artist_name="A", artist_id="a", provider_uri="spotify:track:1")
    assert t.resolved
    assert HttpResponse(204, {}).ok
    assert not HttpResponse(500, {}).ok
    assert PlaylistExport("spotify", "n", track_count=0).fully_matched is False


def test_spotify_exporter_satisfies_the_protocol(profile, catalog, source) -> None:
    """Spotify is now one implementation of `export.base.Exporter`, not the export
    path itself — the seam #54 asks for. A caller holds the protocol."""
    recs = recommend(profile, catalog, source, k=3, lens_strength=0.5)
    client = SpotifyClient(SpotifyToken(access_token="a"), FakeTransport())
    exporter: Exporter = SpotifyExporter(client)

    assert isinstance(exporter, Exporter)
    assert exporter.provider == "spotify"
    assert exporter.export(recs, username="ada").provider == "spotify"
