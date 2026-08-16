# Privacy Notes (DPIA-style)

> Instantiates RESPONSIBLE-TECH-AUDITS §C.
> **Last verified: 2026-08-16 · Recheck cadence: per data-flow change.**
>
> *2026-08-16 — re-verified for ADR 0011 (queer lens).* A second sourced axis is
> now stored. No new egress: the P91 claim is read from the Wikidata entity
> already fetched for P21, so the request count is unchanged and no new question
> is asked of anyone. See "Special-category data" below.
>
> *2026-08-15 — re-verified for FIX-01 (live identity enrichment).* One egress
> module was added (`pipeline/http.py`); one data flow changed from "not shipped"
> to "shipped, opt-in behind `wad ingest --user`". What leaves the machine is
> unchanged in kind: an artist name or MBID goes to a public metadata registry,
> and nothing about *what or when* anyone listened goes with it.

## Data inventory

| Data | Sensitivity | Justification | Storage | Retention |
|------|-------------|---------------|---------|-----------|
| Last.fm username | low (personal) | identifies whose history to fetch | in-memory / local cache | until cache cleared |
| Scrobbles (plays) | personal | the recommendation ground truth | `data/cache.db` (local) | until `make clean` |
| Enriched artist metadata | public, **incl. Art. 9 special-category** (orientation, trans self-identification — ADR 0011) | identity + tags + similarity | `data/cache.db` (local) | re-fetched past the `--ttl-days` horizon; fixture-rewritten on demand |
| API responses | public | rate-limit-respecting cache | `data/cache.db` (local) | overwritten on refetch |
| Playlist export (opt-in) | personal | user-initiated push of the recommended artist names to Spotify | none (sent, not stored) | n/a — only on click |

**Special-category data, stated plainly (ADR 0011).** This document used to say
none was stored. That is no longer true and the change is deliberate: sexual
orientation is GDPR Art. 9 special-category data outright, and the queer lens
records it — together with a trans self-identification where a permitted source
asserted one — about public figures (artists), never about the user, and never
inferred. What has *not* changed: every claim carries a citation, `unknown` is
the answer for almost everyone and is never a negative claim about them, nothing
identity-bearing is ever exported (`tests/test_export_schema.py`), and the cache
is local-only.

The honest consequence is that one defence got weaker. Before, "this repo cannot
produce a list of who is trans" was true of the type system — the vocabulary
could not express it. It is now true of the *process* (no export path, local
cache, no redistribution), which is a real defence but a weaker kind.

## Outbound data flows

There are two product data-flow purposes plus one opt-in diagnostic probe:

1. **Listening-history fetch and identity enrichment** — network-capable Last.fm
   code is confined to `pipeline/lastfm.py` and identity-source fetches to
   `pipeline/http.py` (both asserted by `tests/test_privacy.py`). Since FIX-01
   one product command wires them together: `wad ingest --user <you>`, which is
   opt-in by construction — it requires a username *and* an API key, and no
   other command reaches upstream. What travels outbound is asymmetric, and
   deliberately so: the username goes to Last.fm only (which already holds that
   history), while MusicBrainz and Wikidata receive an artist name or MBID and
   nothing else. Neither registry learns who asked, what was played, or when.
   Responses are cached locally, and identity data is never re-exported
   (`identity-data-ethics.md`, "Non-redistribution").
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
| `pipeline/http.py` | The identity-source transport: MusicBrainz + Wikidata GETs, cached, rate-limited to 1 req/s, sending a `User-Agent` with the operator's `WAD_CONTACT` | `import requests` (lazy, inside `CachedHttpFetcher._get`) |
| `pipeline/doctor.py` | Explicit `wad doctor --check-upstream` reachability probes; never runs by default | `import requests` (lazy, inside the opt-in check) |
| `export/base.py` | The shared exporter seam: PKCE/OAuth helpers plus the **one** live transport used by every playlist provider (Spotify, TIDAL, and any future adapter) | `import requests` (lazy, inside `RequestsTransport.request`) |

A new **playlist provider** does not extend this table: `export/base.py` owns the
only transport in `export/`, so `export/tidal.py` reaches the network exactly as
`export/spotify.py` does — through an injected `HttpTransport` it does not
construct. That is why this list got shorter, not longer, when the second
provider landed.

The same discipline is why FIX-01's live enrichment added **one** row rather
than one per registry: `pipeline/enrich.py` takes its fetcher as a constructor
argument, so MusicBrainz and Wikidata are both reached through the single
transport in `pipeline/http.py`, and a future Discogs enricher would be too.

Adding a new live client requires updating **both** of the following in the same
change, or the new client will fail the merge-blocking privacy gate:

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
  egress exists **only** in the four modules in the "Egress registry /
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
