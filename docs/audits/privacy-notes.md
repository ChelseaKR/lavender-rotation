# Privacy Notes (DPIA-style)

> Instantiates RESPONSIBLE-TECH-AUDITS §C.
> **Last verified: 2026-05-31 · Recheck cadence: per data-flow change.**

## Data inventory

| Data | Sensitivity | Justification | Storage | Retention |
|------|-------------|---------------|---------|-----------|
| Last.fm username | low (personal) | identifies whose history to fetch | in-memory / local cache | until cache cleared |
| Scrobbles (plays) | personal | the recommendation ground truth | `data/cache.db` (local) | until `make clean` |
| Enriched artist metadata | public | identity + tags + similarity | `data/cache.db` (local) | fixture-rewritten on demand; live re-enrichment not shipped |
| API responses | public | rate-limit-respecting cache | `data/cache.db` (local) | overwritten on refetch |
| Playlist export (opt-in) | personal | user-initiated push of the recommended artist names to Spotify | none (sent, not stored) | n/a — only on click |

No special-category data is *inferred*; identity is only ever **sourced** about
public figures (artists), never about the user.

## Outbound data flows

There are two product data-flow purposes plus one opt-in diagnostic probe:

1. **Last.fm-shaped client / enrichment interfaces** — network-capable Last.fm code
   is confined to `pipeline/lastfm.py` (asserted by `tests/test_privacy.py`), but no
   product command currently wires it to a live identity enricher.
2. **Playlist export** (`export/`) — the project's only *user-initiated* egress.
   It is opt-in (nothing leaves on load), runs only when the user clicks
   export/connect, and sends just the recommended **artist names** (a public
   search query) to Spotify to build a playlist. The credential-free formats
   (text / CSV / M3U / JSPF) stay fully local. No listening history, no identity
   data, and no telemetry are transmitted. The live HTTP call is isolated in one
   injectable transport (`export/spotify.py::RequestsTransport`); the rest of the
   flow is exercised offline with a fake transport, so the egress surface is a
   single, auditable function.

   *Secrets:* the Spotify app credentials are read from the environment only
   (`WAD_SPOTIFY_CLIENT_ID`, `WAD_SPOTIFY_CLIENT_SECRET`, `WAD_SPOTIFY_REDIRECT_URI`)
   and the OAuth access/refresh tokens are held in memory for the session, never
   written to disk or committed.
3. **Upstream diagnostics** — `wad doctor --check-upstream` performs explicit,
   opt-in reachability probes and sends no listening history or identity data.

## Egress registry / allowlist (FIX-07)

Single source of truth for every module allowed to open a network connection.
Anything not listed here is, by construction, forbidden from reaching the
network — enforced across `pipeline/`, `recommender/`, `app/`, and `export/`.

| Module | What it does | Live transport |
|--------|---------------|-----------------|
| `pipeline/lastfm.py` | Last.fm scrobble/tag/similarity fetch, cached, rate-limited | `import requests` (lazy, inside the client) |
| `pipeline/doctor.py` | Explicit `wad doctor --check-upstream` reachability probes; never runs by default | `import requests` (lazy, inside the opt-in check) |
| `export/base.py` | The shared exporter seam: PKCE/OAuth helpers plus the **one** live transport used by every playlist provider (Spotify, TIDAL, and any future adapter) | `import requests` (lazy, inside `RequestsTransport.request`) |

A new **playlist provider** does not extend this table: `export/base.py` owns the
only transport in `export/`, so `export/tidal.py` reaches the network exactly as
`export/spotify.py` does — through an injected `HttpTransport` it does not
construct. That is why this list got shorter, not longer, when the second
provider landed.

Adding a new live client (e.g. a FIX-01 MusicBrainz/Discogs/Wikidata HTTP
client) requires updating **both** of the following in the same change, or the
new client will fail the merge-blocking privacy gate:

1. This table.
2. The exact repository-relative module path in `NETWORK_ALLOWED` in
   `tests/test_privacy.py`.

### Two enforcement gates

1. **Source scan (gate 1)** — `tests/test_privacy.py::test_network_access_is_confined_to_api_clients`
   walks every `.py` file under `pipeline/`, `recommender/`, `app/`, and
   `export/` and asserts none of `NETWORK_TOKENS` ("import requests",
   "import httpx", "import urllib3", "import aiohttp", "urllib.request",
   "http.client", "import socket", "webbrowser") appears outside a module in
   the allowlist above. This catches egress statements added anywhere in the
   tree, including string-level additions that haven't run yet.
2. **Runtime socket guard (gate 2)** — the autouse `_no_network` fixture in
   `tests/conftest.py` patches `socket.socket.connect`, `connect_ex`, unconnected
   UDP `sendto`, and `socket.create_connection` to raise for every test. This
   catches *indirect/transitive* egress a text scan can't see (e.g. a call
   reached through a third-party dependency's internals), proving the whole
   suite runs offline by construction rather than by convention. A
   deliberately-added `requests.get(...)` in `app/` fails both gates: gate 1
   because the string appears outside the allowlist, and gate 2 the moment the
   call is actually exercised.

## Handling & commitments

- **Local-first.** Everything lives in a single on-disk SQLite file under `data/`
  (git-ignored). Nothing about the user's listening leaves the machine *except*
  when the user explicitly exports a playlist to Spotify, which sends only the
  recommended artist names (see "Outbound data flows" below).
- **No telemetry / no third-party analytics.** Enforced by source scan:
  `tests/test_privacy.py` asserts no analytics SDK is imported and that network
  egress exists **only** in the three modules in the "Egress registry /
  allowlist" above; the cache uses
  stdlib `sqlite3` only. Backed by a runtime socket guard (see below) so the
  claim holds even for indirect/transitive egress.
- **Exports exclude identity data.** `tests/test_export_schema.py` checks every
  portable format's schema and rendered content so gender, identity basis, and
  provenance cannot silently become a redistributable sidecar.
- **Data minimisation & lineage.** Only what's needed is stored, each row with a
  `fetched_at` timestamp (`pipeline/cache.py`, `tests/test_cache_serde.py`).
- **Deletion path.** `make clean` removes the local cache (`data/*.db`); there is
  no remote copy to chase.
- **Secrets.** Any API key is read from the environment (`WAD_LASTFM_API_KEY`),
  never committed; secret scan is merge-blocking (`scripts/secret-scan.sh`, CI
  gitleaks).

## Enforcement summary

| Commitment | Gate | Where |
|------------|------|-------|
| No telemetry / analytics | auto | `tests/test_privacy.py` |
| Core network confined to API clients (`pipeline`, `recommender`, `app`, `export`) | auto (source scan + socket guard) | `tests/test_privacy.py` · `tests/conftest.py::_no_network` |
| Export egress opt-in & isolated to one transport | auto + review | `tests/test_export.py` (offline fake transport) · this document |
| Lineage timestamps on cache | auto | `tests/test_cache_serde.py` |
| Secrets never in source | auto | secret scan (CI + `make security`) |
| DPIA sign-off | review | this document |
