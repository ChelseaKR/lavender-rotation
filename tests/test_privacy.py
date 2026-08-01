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
                client_id="cid", client_secret="SPOTIFY-SECRET", redirect_uri="http://127.0.0.1/cb"
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
                client_id="cid", redirect_uri="http://127.0.0.1/cb", client_secret="TIDAL-SECRET"
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


def test_cache_uses_only_stdlib_sqlite() -> None:
    cache_src = (Path(pipeline.__file__).parent / "cache.py").read_text(encoding="utf-8")
    assert "import sqlite3" in cache_src
    for token in ("requests", "boto3", "psycopg", "pymongo", "redis"):
        assert token not in cache_src
