# Data Card — Identity & Listening Data

> Instantiates AIEV-23 (datasheet-for-datasets, applied at this project's scale). This is a thin
> card that maps the 7 conventional datasheet sections onto content already committed in
> `docs/audits/identity-data-ethics.md` and `docs/audits/privacy-notes.md`, rather than duplicating
> it — those two files remain the source of truth; this file is the index.
> **Last verified: 2026-07-05 · Recheck cadence: per identity-source API change.**

| Datasheet section | Where it's answered |
|---|---|
| **Motivation** — why does this data exist? | `docs/audits/identity-data-ethics.md` intro: doing values-aware recommendation without inferring, essentializing, or building a misusable gender database. |
| **Composition** — what's in it? | Two distinct datasets, handled differently: (1) a person's own Last.fm scrobbles/tags (personal, local-only — `docs/audits/privacy-notes.md`), and (2) sourced artist-identity metadata from Wikidata P21 / MusicBrainz gender / Discogs lineup (`docs/audits/identity-data-ethics.md` "Permitted identity sources" table). |
| **Collection process** | Two modes. Without `--user`, every command runs against committed Last.fm-shaped and identity fixtures and opens no socket. With `--user`, `lavender ingest` syncs one person's own Last.fm history and resolves identity for the artists it caches against MusicBrainz and Wikidata, paced to roughly one request per second and bounded by `--limit`/`--artist`. It stays on-demand and per-artist by construction; there is no bulk-scrape path. |
| **Preprocessing / cleaning** | Identity resolution is a strict, sourced-only mapping (`pipeline/identity.py`) — no cleaning step ever infers or corrects a label without a new citation; unrecognized/ambiguous values resolve to `unknown` (fail-safe, not fail-open). |
| **Uses** | Ranking and re-ranking artists for one listener; explaining each recommendation's identity basis. Explicitly **not** used to build, export, or redistribute a standalone identity dataset. |
| **Distribution** | Never distributed. Listening history and the resolved-identity cache are local-only, in one SQLite file under the platform user-data directory (`~/Library/Application Support/lavender-rotation/cache.db` on macOS, `%APPDATA%` on Windows, `$XDG_DATA_HOME` elsewhere; `lavender doctor` prints the exact path) — see `docs/audits/privacy-notes.md`. |
| **Maintenance** | The cache supplies TTL expiry, and `lavender refresh --user` re-asks upstream about cached artists and reconciles the pending-corrections ledger against observed changes. Without `--user` it replays the demo fixtures and says so. Re-checks are scheduled locally, every 7 days, by the launchd/cron entry `make schedule` prints (ADR 0013); an upstream retraction is deliberately listed for a human rather than applied — an empty answer is never read as agreement. Citations and fetch dates keep stale rows visible. |

## Known limits (restated from `identity-data-ethics.md`)

Wikidata P21 is sparse and sometimes wrong; MusicBrainz gender is editorial/self-reported; Discogs
lineup data establishes band composition only, never an individual's gender. All three limits are
handled by defaulting to `unknown` rather than guessing — see the no-inference guarantee in
`docs/audits/model-card.md`.
