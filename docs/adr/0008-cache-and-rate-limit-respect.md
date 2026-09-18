# 0008. Local cache + upstream rate-limit respect over aggressive fetching

Date: 2026-07-17

## Status

Accepted (backfilled record of the 2026-05-31 build decision; see ADR 0000 on backfilling)

## Context

The pipeline depends on community-run open-data APIs (Last.fm; MusicBrainz/Wikidata/Discogs for
the enrichment path). Hammering them is both rude to shared infrastructure and a reliability risk
(bans, throttling). Rejected alternative, per `docs/ROADMAP.md` §6: aggressive scraping /
uncached refetching. The original decision predates this record; FIX-04 (2026-07) later gave the
cache a managed lifecycle.

## Decision

All upstream reads go through a local SQLite cache (`pipeline/cache.py`; the file lives in the platform user-data directory resolved by `pipeline/paths.py`) with a
rate-limit-respecting HTTP response cache (TTL-based staleness, `DEFAULT_HTTP_TTL_DAYS = 30`),
schema versioning with forward-refusal (a newer-schema DB fails loudly rather than corrupting),
and scrobble dedupe. The live Last.fm client honors the service's request pacing via an
injectable sleeper (`pipeline/lastfm.py`), which keeps the pacing behavior unit-testable without
real waiting. Cache maintenance is a user-visible verb (`lavender refresh`, `--ttl-days`), not a hidden
side effect.

## Consequences

- Repeat runs are fast and mostly offline; the demo world and the whole test suite run with zero
  network (enforced by the egress guards in `tests/test_privacy.py` / `tests/conftest.py`).
- Cached identity data can go stale; TTL expiry plus `lavender refresh`'s identity-label change report
  make re-enrichment routine and auditable rather than silent.
- Any future live client (the deferred FIX-01 path) inherits this ADR: cache-through and
  documented rate-limit compliance are preconditions, not optimizations (see the External API
  rate-limit row in `docs/ROADMAP.md` §7's metrics table).
