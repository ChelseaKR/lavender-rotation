# 0013. Periodic re-enrichment is scheduled on the operator's machine, not in CI

Date: 2026-09-05

## Status

Accepted

## Context

`lavender refresh --user` closes the upstream-correction round-trip: it re-asks MusicBrainz and
Wikidata about artists already in the cache, and reconciles the pending-corrections ledger only
against an observation that actually came back (`RefreshOutcome.upstream_answered` — a silent
upstream is reported as unreachable, never as agreement, and never overwrites a citation).

Everything around it was built and nothing ran it. `docs/ROADMAP.md` §11 and
`docs/RESEARCH-ROADMAP.md` rows R2/E9 recorded the same gap in the same words: *what remains
deferred is periodic re-enrichment; there is no scheduler, so a re-check is an operator running
the command again.* `docs/audits/ai-risk-register.md` AIR-8 carried it as a partly-closed risk.

That gap is a correctness problem, not a convenience one. This project asks artists to fix a
wrong claim about themselves at the source and promises the fix will reach the local catalog. If
nothing re-asks on a schedule, that promise depends on someone remembering.

The obvious move — a GitHub Actions cron, which is how every other recurring job in this repo runs
— is the wrong one here, and it is worth writing down why rather than discovering it later:

- **A hosted runner has no cache to refresh.** The thing being re-enriched is a SQLite cache of
  *your* listening history in the platform user-data directory (`pipeline/paths.py`). A fresh
  runner has an empty one, so a scheduled workflow would either refresh nothing or need the cache
  uploaded to it.
- **Uploading it would break the local-first promise.** `docs/audits/privacy-notes.md` says
  listening data stays on the machine. A personal listening profile in CI storage is exactly the
  thing that document rules out, and it would be traded for no capability at all.
- **A workflow that cannot reach its data is worse than no scheduler.** It reports green weekly
  for work that did not happen. This repo has already fixed that shape of bug twice — the a11y
  gate that regenerated the page before auditing it (#71), and the eval target that rewrote the
  report it was supposed to check (`eval-check`). A third instance is not worth shipping.

## Decision

Periodic re-enrichment is a **local** schedule the operator installs, on a **weekly** cadence.

- `make refresh LAVENDER_USER=<you>` is the run. It is `lavender refresh --user` with the CLI's
  own bounds; the Makefile deliberately restates none of them, so the scheduled run and the
  hand-run are the same run.
- `make schedule LAVENDER_USER=<you>` prints the scheduler entry for this checkout — a launchd
  user agent on macOS, a crontab line elsewhere — with absolute paths filled in.
  `scripts/refresh_schedule.py` renders it. It prints; it installs nothing. A job that acts on
  your behalf should be read and pasted, not written into your login session by a build target.
- **The cadence lives in one place** (`CADENCE_DAYS = 7`) and the prose that states it is held to
  that constant by `scripts/docs_figures.py`, so the documented cadence cannot drift from the
  rendered one.
- **No credential is ever rendered into a scheduler entry.** A launchd agent under
  `~/Library/LaunchAgents` is a plain file and a crontab is readable by root and by anything that
  backs up a home directory. Both entries source a mode-600 env file the operator creates once;
  `tests/test_refresh_schedule.py` asserts no rendered entry carries a credential value.
- **A scheduled run is logged and its failures are real.** The entry redirects to a log under the
  platform's log directory. `lavender refresh` exits non-zero when nothing came back and says the
  upstream was unreachable, so a red scheduled run means something, and silence in the log is not
  evidence that upstream agreed.

## Why weekly, and what weekly buys

One run is bounded — `--limit` defaults to 100 against a ~1 req/s upstream — and
`Cache.stalest_artist_ids` orders by `fetched_at`, so consecutive runs rotate through the catalog
rather than re-walking its head. A schedule therefore has a **sweep period**: with a catalog of
*N* artists and a limit of *L*, every artist is revisited about every `ceil(N / L)` runs.

Weekly puts that sweep in a useful relationship with the HTTP cache. `DEFAULT_HTTP_TTL_DAYS` is
30 (ADR 0008), so a re-visit that arrives sooner than 30 days is served from the response cache
and learns nothing new. At `L = 100` and one run a week, any catalog of roughly 430 artists or
more has a sweep longer than the TTL, and every visit is a genuine re-ask. A smaller catalog
sweeps faster than the TTL; the fix there is a smaller `--limit` (`REFRESH_ARGS="--limit 25"`),
not a shorter TTL — lowering `--ttl-days` on a *bounded* run expires the whole response cache,
including the similarity graph `lavender recommend --user` reads, while refetching only the
artists that run happened to touch.

Daily was considered and rejected on the same arithmetic: it multiplies requests to a
volunteer-run service by seven and, on any catalog under about 3,000 artists, spends most of them
re-reading the response cache.

## Consequences

- An upstream correction now reaches the local catalog without anyone remembering, on a stated
  cadence, with a log to read.
- The schedule is per-machine and unversioned by design: it is installed once from a rendered
  entry, and re-rendering after a repo move is `make schedule` again.
- CI still never touches live upstream data, and `tests/conftest.py`'s socket guard is unchanged —
  the schedule adds no network path to the suite.
- The remaining half of R2 is unchanged and still open: a public artist-facing opt-out/intake
  route. Scheduling the re-ask does not create one.
