"""TIDAL playlist export — offline, with a fake transport.

No socket is opened here, and none can be: ``tests/conftest.py`` installs a
runtime guard that turns any real connection attempt into a ``RuntimeError``.
Every branch below runs against :class:`FakeTidalTransport`, which speaks the
JSON:API envelopes TIDAL's documented endpoints return.

What these tests prove: the OAuth flow's shape, the JSON:API unwrapping, batching,
the unmatched-artist contract, and that nothing from the listening profile is
ever put on the wire. What they cannot prove: that TIDAL's live service still
uses these exact paths and field names — nobody here holds TIDAL app
credentials. That limit is stated in ``export/tidal.py``'s docstring too, rather
than left for a reader to discover.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from typing import Any, Optional

import pytest
from export import tidal as td
from export.base import Exporter, HttpResponse, PkcePair
from export.models import ExportError
from recommender.hybrid import recommend

_ENV = {
    "WAD_TIDAL_CLIENT_ID": "cid",
    "WAD_TIDAL_REDIRECT_URI": "http://127.0.0.1:8080/callback",
}


class FakeTidalTransport:
    """An in-memory HTTP double routing the handful of TIDAL endpoints we use."""

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
        if url == td.TOKEN_URL:
            return HttpResponse(
                200,
                {
                    "access_token": "access-123",
                    "token_type": "Bearer",
                    "scope": "playlists.write",
                    "expires_in": 3600,
                    "refresh_token": "refresh-123",
                },
            )
        if url.endswith("/users/me"):
            return HttpResponse(200, {"data": {"id": "user42", "type": "users"}})
        if "/searchResults/" in url:
            raw = url.split("/searchResults/")[1].split("?")[0]
            query = urllib.parse.unquote(raw)
            if any(name.lower() in query.lower() for name in self.missing):
                return HttpResponse(200, {"data": {}, "included": []})
            return HttpResponse(
                200,
                {
                    "data": {"id": "sr-1", "type": "searchResults"},
                    # Deliberately not first: the client must select by TYPE, not
                    # by position, or it will happily add an artist page as a track.
                    "included": [
                        {"id": "artist-9", "type": "artists"},
                        {"id": f"track-{abs(hash(query)) % 10_000}", "type": "tracks"},
                    ],
                },
            )
        if method == "POST" and url.endswith("/playlists"):
            return HttpResponse(201, {"data": {"id": "playlist99", "type": "playlists"}})
        if method == "POST" and url.endswith("/relationships/items"):
            return HttpResponse(201, {})
        return HttpResponse(404, {})  # pragma: no cover - defensive default


def _authorized_client(
    missing: tuple[str, ...] = (),
) -> tuple[td.TidalClient, FakeTidalTransport]:
    transport = FakeTidalTransport(missing_artists=missing)
    creds = td.TidalCredentials.from_env(_ENV)
    token = td.TidalOAuth(creds, transport).exchange_code("code-1", code_verifier="v")
    return td.TidalClient(token, transport), transport


# --- credentials -------------------------------------------------------------


def test_credentials_come_from_env_and_name_what_is_missing() -> None:
    with pytest.raises(ExportError) as excinfo:
        td.TidalCredentials.from_env({"WAD_TIDAL_CLIENT_ID": "cid"})
    assert "WAD_TIDAL_REDIRECT_URI" in str(excinfo.value)


def test_client_secret_is_optional_for_a_public_pkce_client() -> None:
    """A native client authenticates the token exchange with PKCE alone. Sending an
    empty Basic header would be worse than sending none."""
    creds = td.TidalCredentials.from_env(_ENV)
    assert creds.client_secret == ""
    assert creds.token_auth_header() is None


def test_client_secret_when_present_becomes_basic_auth() -> None:
    creds = td.TidalCredentials.from_env({**_ENV, "WAD_TIDAL_CLIENT_SECRET": "shh"})
    header = creds.token_auth_header()
    assert header is not None and header.startswith("Basic ")


# --- OAuth -------------------------------------------------------------------


def test_authorize_url_carries_pkce_challenge_and_state() -> None:
    creds = td.TidalCredentials.from_env(_ENV)
    pair = PkcePair.generate()
    url = td.TidalOAuth(creds, FakeTidalTransport()).authorize_url(
        "state-xyz", code_challenge=pair.challenge
    )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith(td.AUTH_URL)
    assert query["state"] == ["state-xyz"]
    assert query["code_challenge"] == [pair.challenge]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == [" ".join(td.DEFAULT_SCOPES)]


def test_authorize_url_refuses_an_empty_state() -> None:
    """State is the CSRF defence; generating one and then not sending it is the
    failure mode this guard exists to make impossible."""
    creds = td.TidalCredentials.from_env(_ENV)
    with pytest.raises(ValueError):
        td.TidalOAuth(creds, FakeTidalTransport()).authorize_url("")


def test_exchange_code_sends_the_verifier_and_returns_a_token() -> None:
    transport = FakeTidalTransport()
    creds = td.TidalCredentials.from_env(_ENV)
    token = td.TidalOAuth(creds, transport).exchange_code("code-1", code_verifier="verifier-1")
    assert token.access_token == "access-123"
    sent = transport.calls[0]["data"]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code_verifier"] == "verifier-1"


def test_refresh_reuses_the_old_refresh_token_when_none_is_returned() -> None:
    class NoRefresh(FakeTidalTransport):
        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            super().request(method, url, **kwargs)
            return HttpResponse(200, {"access_token": "new-access"})

    creds = td.TidalCredentials.from_env(_ENV)
    token = td.TidalOAuth(creds, NoRefresh()).refresh("old-refresh")
    assert token.access_token == "new-access"
    assert token.refresh_token == "old-refresh"


def test_token_response_without_an_access_token_is_an_error() -> None:
    with pytest.raises(ExportError):
        td.TidalToken.from_body({"token_type": "Bearer"})


def test_failed_token_request_raises_with_the_status() -> None:
    class Denied(FakeTidalTransport):
        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            super().request(method, url, **kwargs)
            return HttpResponse(401, {})

    creds = td.TidalCredentials.from_env(_ENV)
    with pytest.raises(ExportError) as excinfo:
        td.TidalOAuth(creds, Denied()).exchange_code("bad")
    assert "401" in str(excinfo.value)


# --- API client --------------------------------------------------------------


def test_current_user_id_unwraps_the_json_api_envelope() -> None:
    client, _ = _authorized_client()
    assert client.current_user_id() == "user42"


def test_missing_user_id_raises_instead_of_returning_empty() -> None:
    class NoUser(FakeTidalTransport):
        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            if url.endswith("/users/me"):
                return HttpResponse(200, {"data": {}})
            return super().request(method, url, **kwargs)

    transport = NoUser()
    token = td.TidalToken(access_token="a")
    with pytest.raises(ExportError):
        td.TidalClient(token, transport).current_user_id()


def test_search_selects_by_resource_type_not_by_position() -> None:
    """The fake returns an artist resource before the track. Picking `included[0]`
    would put an artist page in the playlist and look like it worked."""
    client, _ = _authorized_client()
    track_id = client.find_track_id("Big Thief Not")
    assert track_id is not None and track_id.startswith("track-")


def test_search_miss_returns_none_rather_than_raising() -> None:
    """A catalogue genuinely may not carry an artist in a market. That is an
    unmatched artist to report, not an error to abort the whole export."""
    client, _ = _authorized_client(missing=("Nobody",))
    assert client.find_track_id("Nobody Whatever") is None


def test_search_is_scoped_to_a_country_and_url_encodes_the_query() -> None:
    client, transport = _authorized_client()
    client.find_track_id("Sault / Nine")
    url = transport.calls[-1]["url"]
    assert "%2F" in url or "%2f" in url, "the slash must be percent-encoded, not a path segment"
    assert f"countryCode={td.DEFAULT_COUNTRY}" in url


def test_create_playlist_defaults_to_private() -> None:
    """An export of someone's listening-derived recommendations is theirs. A tool
    does not publish it on their behalf unless they ask."""
    client, transport = _authorized_client()
    client.create_playlist("My list", "why")
    attributes = transport.calls[-1]["json"]["data"]["attributes"]
    assert attributes["accessType"] == "PRIVATE"

    client.create_playlist("My list", "why", public=True)
    assert transport.calls[-1]["json"]["data"]["attributes"]["accessType"] == "PUBLIC"


def test_missing_playlist_id_raises() -> None:
    class NoId(FakeTidalTransport):
        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            if method == "POST" and url.endswith("/playlists"):
                return HttpResponse(201, {"data": {}})
            return super().request(method, url, **kwargs)

    with pytest.raises(ExportError):
        td.TidalClient(td.TidalToken(access_token="a"), NoId()).create_playlist("x")


def test_add_tracks_batches_within_the_cap() -> None:
    client, transport = _authorized_client()
    client.add_tracks("p1", [f"t{i}" for i in range(td._ADD_BATCH + 3)])
    adds = [c for c in transport.calls if str(c["url"]).endswith("/relationships/items")]
    assert len(adds) == 2
    assert len(adds[0]["json"]["data"]) == td._ADD_BATCH
    assert len(adds[1]["json"]["data"]) == 3
    assert all(item["type"] == "tracks" for item in adds[0]["json"]["data"])


def test_api_error_status_is_surfaced() -> None:
    class Broken(FakeTidalTransport):
        def request(self, method: str, url: str, **kwargs: Any) -> HttpResponse:
            if url.endswith("/users/me"):
                return HttpResponse(503, {})
            return super().request(method, url, **kwargs)

    with pytest.raises(ExportError) as excinfo:
        td.TidalClient(td.TidalToken(access_token="a"), Broken()).current_user_id()
    assert "503" in str(excinfo.value)


# --- end-to-end export -------------------------------------------------------


def test_export_creates_a_playlist_and_preserves_ranking(profile, catalog, source) -> None:
    recs = recommend(profile, catalog, source, k=5, lens_strength=0.5)
    client, transport = _authorized_client()

    result = td.export_recommendations(recs, client, username="ada")

    assert result.provider == "tidal"
    assert result.playlist_id == "playlist99"
    assert result.playlist_url == "https://tidal.com/playlist/playlist99"
    assert result.track_count == len(recs)
    assert result.fully_matched
    searched = [
        urllib.parse.unquote(str(c["url"]).split("/searchResults/")[1].split("?")[0])
        for c in transport.calls
        if "/searchResults/" in str(c["url"])
    ]
    assert len(searched) == len(recs)
    for query, rec in zip(searched, recs, strict=True):
        assert rec.artist.name in query


def test_export_reports_unmatched_artists_instead_of_dropping_them(
    profile, catalog, source
) -> None:
    recs = recommend(profile, catalog, source, k=5, lens_strength=0.5)
    absent = recs[0].artist.name
    client, _ = _authorized_client(missing=(absent,))

    result = td.export_recommendations(recs, client)

    assert absent in result.unmatched
    assert result.matched_count == result.track_count - 1
    assert not result.fully_matched


def test_export_of_an_empty_recommendation_set_is_an_error() -> None:
    client, _ = _authorized_client()
    with pytest.raises(ExportError):
        td.export_recommendations([], client)


def test_export_sends_only_artist_and_track_names(profile, catalog, source) -> None:
    """The egress promise, asserted rather than described: nothing from the
    listening profile reaches the wire — no play counts, no scrobble timestamps,
    no identity field, not the username unless it is in the playlist title the
    user chose."""
    recs = recommend(profile, catalog, source, k=5, lens_strength=0.5)
    client, transport = _authorized_client()
    td.export_recommendations(recs, client, playlist_name="Neutral title")

    wire = " ".join(f"{c['url']} {c['json']} {c['data']}" for c in transport.calls).lower()
    for artist in catalog:
        for forbidden in (str(getattr(artist, "identity", "")), str(getattr(artist, "id", ""))):
            if len(forbidden) > 6 and forbidden.lower() not in artist.name.lower():
                assert forbidden.lower() not in wire
    assert "scrobble" not in wire
    assert "play_count" not in wire
    assert "playcount" not in wire


def test_tidal_exporter_satisfies_the_protocol(profile, catalog, source) -> None:
    """The whole point of the seam: a caller can hold `Exporter`, not `tidal`."""
    recs = recommend(profile, catalog, source, k=3, lens_strength=0.5)
    client, _ = _authorized_client()
    exporter: Exporter = td.TidalExporter(client)

    assert isinstance(exporter, Exporter)
    assert exporter.provider == "tidal"
    assert exporter.export(recs, username="ada").provider == "tidal"
