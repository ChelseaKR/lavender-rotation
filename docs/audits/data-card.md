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
| **Collection process** | The shipped product uses committed Last.fm-shaped and identity fixtures. The library has source interfaces and parsers, but live username-to-enriched-catalog orchestration is explicitly deferred; it must remain on-demand rather than become a bulk scrape. |
| **Preprocessing / cleaning** | Identity resolution is a strict, sourced-only mapping (`pipeline/identity.py`) — no cleaning step ever infers or corrects a label without a new citation; unrecognised/ambiguous values resolve to `unknown` (fail-safe, not fail-open). |
| **Uses** | Ranking and re-ranking artists for one listener; explaining each recommendation's identity basis. Explicitly **not** used to build, export, or redistribute a standalone identity dataset. |
| **Distribution** | Never distributed. Listening history and the resolved-identity cache are local-only (`data/cache.db`, git-ignored) — see `docs/audits/privacy-notes.md`. |
| **Maintenance** | The cache supplies TTL expiry and `refresh_catalog` supports a dependency-injected source/enricher, but `lavender refresh` currently replays demo fixtures and performs no upstream request. Automated correction fold-back remains open with FIX-01; citations/fetch dates make stale rows visible in the meantime. |

## Known limits (restated from `identity-data-ethics.md`)

Wikidata P21 is sparse and sometimes wrong; MusicBrainz gender is editorial/self-reported; Discogs
lineup data establishes band composition only, never an individual's gender. All three limits are
handled by defaulting to `unknown` rather than guessing — see the no-inference guarantee in
`docs/audits/model-card.md`.
