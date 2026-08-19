# Lavender Rotation

**A demo-first music-discovery engine that surfaces new women, nonbinary, and female-fronted artists through an explicit values lens — including a queer lens for sourced queer women and nonbinary artists.** It combines collaborative and content signals with a sourced-identity re-ranker. Identity is never inferred, and "unknown" is a normal, first-class answer.

**Trans women are women here — explicitly.** The three terms in the tagline are not redundant; they cover three different shapes: *women* (solo artists whose sourced self-identification is woman — cis or trans, with no distinction drawn anywhere in the data model), *nonbinary* artists (represented as nonbinary, never folded into another category), and *female-fronted* (band-composition metadata: an act whose sourced lineup/role data shows a woman — cis or trans — fronting it, which is a fact about that lineup, never a claim about the band's other members). A trans woman artist whose self-identification is sourced is surfaced as a woman, full stop. A band fronted by a sourced nonbinary artist is described as fronted by a nonbinary artist — the lens surfaces it, and no one is relabelled to get there.

**Status:** `Beta` · **Track:** Personal (data/ML + small web app) · **License:** MIT · **Data:** personal/local

## Quickstart

```sh
make install && make dev   # offline demo mode — no API key needed
make verify                # run the full merge gate locally
```

Demo mode ships a clearly-labeled synthetic world (`pipeline/demo.py`), so you can
explore recommendations, the fairness/exposure panel, and per-pick explanations
without an account anywhere.

To run it against your own listening history instead, sync once and then point any
recommendation surface at your username:

```sh
export LAVENDER_LASTFM_API_KEY=...     # https://www.last.fm/api/account/create
export LAVENDER_CONTACT=you@example.org  # sent in the User-Agent MusicBrainz asks for
lavender ingest --user <your-lastfm-username>
lavender recommend --user <your-lastfm-username>
lavender recommend --user <you> --lens 1.0 --hide-sourced-men   # strongest lens, plus the filter
```

`lavender ingest` is the **only** command that reaches upstream: it syncs your scrobbles
from Last.fm (incrementally — a second run fetches only what is new), resolves
identity for the artists it caches against MusicBrainz and Wikidata, and enriches
the candidates it can reach from your taste. Expect the first run to take a few
minutes; it paces itself to one request per second and caches every response, so
later runs are fast and cost the registries nothing. Everything it learns stays in
your local cache. Artists it cannot resolve to exactly one upstream record stay
`unknown`, which costs them nothing in the ranking.

## Why it matters
Your library leans toward women and female-fronted bands by taste, but no recommender helps you lean into that on purpose without either ignoring identity entirely or guessing it crudely. Doing this *well* — sourced, transparent, non-essentialist — is the whole point and the interesting part.

## What it does
- **Builds listening profiles** from your Last.fm history — paginated, incremental, and resumable — or from the offline demo world when you have no account to hand.
- **Hybrid recommendations:** collaborative similarity + content/tags, then a values-aware re-rank.
- **Two declared lenses,** chosen per run with `--lens-name`: `women-nonbinary` (the default) and `queer` — sourced queer women plus sourced nonbinary artists ([ADR 0011](./docs/adr/0011-queer-lens-and-the-trans-vocabulary-amendment.md)). Each is a `LensSpec` manifest carrying its own aligned set, boost bound, rationale, and honest harms note. Sourced queerness is sparse and skews toward the already-famous, Anglophone, living and out, so the queer lens boosts rather than filters and `unknown` never reads as "not queer".
- **Sourced identity, never inferred:** identity basis is shown and cited; woman means woman, cis or trans, with no distinction drawn; nonbinary is represented properly; unknown artists are surfaced on musical merit alone.
- **Explains every pick:** a shared "Why this artist" view — why (which signals) + identity basis + provenance (the *raw value each source asserted*, never inferred).
- **Export your picks:** push the current set to a **Spotify** or **TIDAL** playlist (env-configured OAuth, PKCE, user-initiated), or download a portable, account-free track list (plain text / CSV / M3U / JSPF). The portable file is the today-path for *any* platform without a native adapter: it imports directly into most players and into transfer tools such as Soundiiz or TuneMyMusic, and it needs no account and no credentials from you. Apple Music (paid Developer Program membership) and Qobuz (partner approval) are externally gated — [#54](https://github.com/ChelseaKR/lavender-rotation/issues/54). Every exporter sends artist and track names only; nothing from your listening profile goes with them.
- **Local-first:** your listening history stays yours. Sanctioned egress is limited to explicit Last.fm fetches, per-artist identity lookups against MusicBrainz/Wikidata (which receive an artist name or MBID and learn nothing about who asked or what they played), opt-in upstream diagnostics, and user-initiated playlist export (artist/track names only).

## Guardrails

These are hard rules, each enforced by a merge-blocking test (see
`tests/test_no_inference.py`, the centrepiece):

- **Never infer an artist's gender or identity** from name, voice, image, genre, or any heuristic — identity labels come only from cited self-identification sources (artist statement, sourced Wikidata P21 claim, MusicBrainz gender field) and must carry that citation. The AST leg of the guardrail test walks **every** function in **every** `pipeline/` module, not a named subset; the few that legitimately handle content tags are listed with a reason and held to a stricter check; and `recommender/`, `app/`, and `export/` are asserted to construct no identity objects at all, so an inference path cannot be introduced by moving it out of scope.
- **Woman includes trans women explicitly** — sourced self-identification is the only test, and the `Gender` vocabulary draws no cis/trans distinction: a trans woman is `Gender.WOMAN`, full stop. [ADR 0011](./docs/adr/0011-queer-lens-and-the-trans-vocabulary-amendment.md) narrowed this guardrail, and the difference is worth reading: a *separate* sourced axis records a trans self-identification when a permitted source asserted one, so the queer lens can surface trans women who have not publicly discussed their orientation. It is tri-state and never `False` — "not recorded as trans" is never "recorded as cis" — and it reads a raw asserted value the cache already stored rather than fetching anything new.
- **"Unknown" is first-class** and must never reduce, down-rank, or drop a recommendation; the values lens only ever boosts. This binds the opt-in `--hide-sourced-men` filter too — the one mechanism here that can make an artist disappear. It removes only a *positive* sourced claim (an artist sourced as a man, or an act whose sourced fronting lineup is entirely sourced men) and never an absent one, because filtering on "not values-aligned" would delete every unknown artist — disproportionately the less-documented ones, which on a gender-imbalanced upstream skews against exactly the artists the lens is for. An artist sourced as a gender the lens does not boost (`Gender.OTHER`) holds their pure-taste position too. No artist's score is ever reduced. A sourced man's list *position* can move down — that is the one thing this lens re-allocates, and the lens's harms note says so rather than denying it.
- **"Female-fronted" is band-composition metadata** (lineup/role), sourced not guessed, and never widened: it means only that a front-person's *own* sourced gender is a woman's. A front-person's gender is rendered as the source stated it, never collapsed into the band-level word.
- **Every recommendation shows its work:** why + identity basis + source.
- **No redistribution of a scraped musician-identity dataset** — minimize, cite, keep correctable.

## Project status

The offline demo and full pipeline are implemented and gated: `make verify` runs
formatting/lint/SAST, strict typing, 756 tests at 96% coverage, dependency and
secret scans, axe/pa11y renders plus browser-driven keyboard/reflow/reduced-motion
specs (Playwright, required in CI), offline multiworld evaluation with
regression/fairness gates, and the i18n declaration gate. CodeQL, zizmor, OSV,
Scorecard, release, and CI workflows all run hosted.

Live username-to-recommendation orchestration **closed with FIX-01**: `lavender ingest
--user <you>` syncs a real history and resolves identity from MusicBrainz/Wikidata
through one allowlisted HTTP seam, and `--user` on `recommend`/`report`/`export`
reads that cached world back. The live path is unit-gated offline against recorded
payloads (`tests/test_live_enrichment.py`) rather than against the network, so the
suite still opens no socket. Still open: review-gated manual screen-reader/keyboard
sign-offs, and the two live-mode limits below — see [`docs/audits/`](./docs/audits/).

`lavender refresh --user <you>` closes the other half: it re-asks MusicBrainz and
Wikidata about artists already in your cache, so an edit that landed upstream since
your ingest — including one you filed yourself — can reach the local catalog, and the
corrections ledger finally *acts* on an upstream observation. A refresh that moves
only a retrieval date is not evidence of an edit, and a change to some *other* value
marks the row superseded rather than deleting it. Without `--user` the command is
unchanged and still prints its **demo-only** banner.

The live leg refuses to read silence as agreement. The enricher renders every upstream
failure as "no evidence", which is indistinguishable from "upstream holds no claim" —
harmless on ingest, where both mean `unknown`, but on refresh it would overwrite a
citation you paid for and report zero changes doing it. So only an artist that comes
back *carrying sources* is written; anything else keeps its existing label **and** its
original `fetched_at`, because that date is a claim the artist was checked that day.
A run where nothing came back exits non-zero, says the upstream was unreachable, and
reconciles no corrections. A genuine upstream retraction is therefore not applied
automatically — it is listed for you to act on with `lavender corrections add`, which
is the direction this project errs in everywhere else too.

Bounded on purpose: upstream is ~1 req/s and a real catalog runs to thousands of
artists, so `--limit` (default 100) caps a run and `--artist` targets one. Re-running
resumes — everything already fetched is served from the HTTP cache until `--ttl-days`
ages it out. There is still no scheduler; a re-check is you running the command again.

The second live-mode limit is coverage of *your* upstream data, not of this code:
Last.fm supplies an MBID for only some artists, and a name that matches two
MusicBrainz records — or none exactly — resolves to `unknown` rather than to a
guess. That is the guardrail working, and `unknown` artists are still recommended on
musical merit; expect a real listening history to produce more of them than the demo
world does.

This project is built in the open: [`docs/RESEARCH-ROADMAP.md`](./docs/RESEARCH-ROADMAP.md),
[`docs/ideation/`](./docs/ideation/), and [`docs/USER-RESEARCH.md`](./docs/USER-RESEARCH.md)
are working notes, published deliberately and labeled for what they are (the
user-research personas are synthetic, and say so at the top).

## Observability
**Tier C** — OTel tracing is out of scope for this local tool. The CLI configures
structured stage/timing logs, supports `--log-format json`, and `lavender doctor`
reports local configuration/cache health with opt-in upstream probes.

## AI-evaluation status
In scope per `AI-EVALUATION-STANDARD.md` §0 as a classical-ML recommender: no LLM,
RAG, generation, or judge. Direct runtime dependency is `requests`; Streamlit/pandas
live in the app extra and test/audit tools live in the dev group. §1–3's
LLM/RAG/judge gates are dormant. The gate that **is** active and merge-blocking:
the offline eval must beat the popularity baseline (`make eval`, `docs/audits/eval-report.json`).
The first LLM SDK import anywhere in this repo flips this status to `APPLIES` in full and activates
§1–3. See `docs/RESPONSIBLE-TECH-AUDITS.md` for the full AI-governance picture.

## Standards Conformance
Inherits [`/STANDARDS`](../STANDARDS/). Per-standard declarations (Documentation Standard's
"a repo must declare Applies/N/A for every standard, not just inherit silently" rule):

| # | Standard | Status | Notes |
|---|----------|--------|-------|
| 1 | Quality & Metrics | Applies | ROADMAP §7 metrics ledger; `make verify` enforces the current gates. |
| 2 | Code Quality | Applies | Ruff, strict mypy, per-module coverage floor, PEP 735 dev group, and complexity checks are active. Mutation testing (CQ-47) gates the safety-critical modules — `make mutation` fails under 70% mutants killed; weekly + on-demand in CI (`mutation.yml`). |
| 3 | Security & Supply-Chain | Applies — **ASVS 5.0 Level 1** | No auth / no multi-user surface, so L2 controls are N/A (no server); see `docs/RESPONSIBLE-TECH-AUDITS.md` §F |
| 4 | CI/CD | Applies | CODEOWNERS, workflows, and the live main ruleset are configured; hosted execution restored 2026-07-19 (repo made public — free runner minutes). |
| 5 | Release & Versioning | Applies — **release-producing, unreleased** | No tag/release exists yet; see `CHANGELOG.md` and `SECURITY.md` for the current stance |
| 6 | Accessibility | Applies | axe gate blocking (0 violations) + Playwright keyboard/reflow/reduced-motion specs (`tests/test_e2e_a11y.py`); the committed render in `docs/audits/` is byte-gated against the renderer (`tests/test_committed_render.py`), so the page you can browse is the page that was audited; Lighthouse not wired; manual screen-reader + keyboard sign-offs pending the first release, and `app/dashboard.py` is covered by neither gate (`docs/audits/accessibility-2026-07-17.md`) |
| 7 | Observability | Applies — **Tier C** | See Observability section above |
| 8 | Internationalization | **N/A — single-user operator-only output** | Scope decision in `docs/I18N.md`, self-enforced via `scripts/i18n-gate.sh` |
| 9 | AI Evaluation | Applies — **narrow** | See AI-evaluation status above |
| 10 | Documentation | Applies | ADR log, documentation audit, citation validation, and staleness gate are active. |
| 11 | Responsible-Tech Framework | Applies | Audits A–F committed — `docs/RESPONSIBLE-TECH-AUDITS.md` |

Open or human-gated gaps are dispositioned in `docs/RESEARCH-ROADMAP.md` and
`docs/ideation/`; they are not represented as shipped features.

## Support

This is independent, unpaid work. If it has been useful to you, you can
<a href='https://ko-fi.com/T6T6GMYTU' target='_blank'><img height='36' style='border:0px;height:36px;' src='https://storage.ko-fi.com/cdn/kofi6.png?v=6' border='0' alt='Buy Me a Coffee at ko-fi.com' /></a>
