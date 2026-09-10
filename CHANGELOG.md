# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/); this project
intends to adhere to [Semantic Versioning](https://semver.org/) once a first version is tagged.

**Release stance:** this is unreleased pre-1.0 development software — see `SECURITY.md` and
`CITATION.cff`. There is no tagged release yet, so everything below lives under `[Unreleased]`.
When `v0.1.0` is tagged, its entries move to a new `## [0.1.0] - YYYY-MM-DD` section dated to the
tag, not backfilled to an earlier commit date.

## [Unreleased]

### Added

- `--json` on `report`, `feedback`, `refresh`, `eval` and `runs`, completing the
  set every user-facing command offers (#121). Each emits one versioned document
  against a committed schema under `schemas/`, and each carries a distinction the
  text rendering had been keeping in prose:
  - `refresh` in demo mode reports every upstream count as `null` rather than `0`.
    A demo run publishing `attempted: 0` beside `answered: false` would be
    indistinguishable from a live run that reached nothing, which is the single
    distinction `RefreshOutcome` exists to keep.
  - `eval` reports `regressed_vs_baseline: null` when no baseline file was
    present — `false` would be a claim about a comparison nobody ran — and lists
    every retention guarantee that passed only because its segment was empty.
  - `runs list` keeps a manifest it could not read in `unreadable` rather than in
    `runs`, with its own name and error.
  - `report --json` still writes the HTML page, and says where it went and how
    many bytes it wrote.
- `schemas/{report,feedback,refresh,eval,runs}.schema.json`, generated from the
  document definitions by `scripts/gen_schemas.py` and compared byte for byte in
  the suite.

- **The a11y gate reports what it examined, per rule family (#139).** `make a11y`'s
  offline checker printed `a11y: 0 violations` and nothing else — the same
  sentence over a page with twelve interactive controls and over a page with
  none. The audited artifact is the second kind: `app/render.py` emits no
  `<button>`, `<input>`, `<select>`, `<textarea>` or `<form>` at all, so every
  rule that only has something to say about a control ran over nothing and
  reported a pass, while `app/dashboard.py` builds **12** interactive widgets
  that Streamlit renders at run time and no gate here reaches. That limit was
  disclosed in README conformance row 6 and in `tests/test_e2e_a11y.py`'s
  docstring; neither is reachable from a green line, which is where a reader
  meets the gate.
  - Every run now prints a census: **7 rule families**, each with its own
    denominator. On the committed render that reads `1 of 1 documents`,
    `19 of 19 headings`, `8 of 8 links`, `3 of 3 tables`, `32 of 32 <th>`, and
    `not_applicable` for `images` and `controls` — each with a written reason.
    **63 elements examined**, where the old line stated none.
  - **A family that examined nothing is never a pass.** It reports
    `not_applicable` with a reason, because "no findings" and "no inputs" are
    otherwise byte-identical verdicts.
  - **Each family declares exactly one of a floor or a written reason**, and the
    suite holds the table to that either/or, so a family added later cannot skip
    the question. The floors mean a render that stops emitting tables — or a
    selector that stops matching — now fails instead of passing on nothing:
    measured, a page with no tables produced **0 violations and 2 failing
    families**, where before it produced 0 violations and exit 0.
  - Every floor is **1**, deliberately. A floor equal to today's count (3 tables,
    32 `<th>`) is a hand-maintained counter, and this portfolio has jammed its
    own merge queue on those.
  - The unreached widget count is **measured from `app/dashboard.py`**, not
    typed, and prints as "not counted here" rather than `0` when that file
    cannot be read — a count of zero unreached controls is exactly the false
    reassurance this change exists to stop giving.
  - An interactive control in an audited document is now **refused**, because
    this checker has no accessible-name, label-association or focus-order rule;
    counting one as examined would report `1 of 1 controls — pass` from a
    checker that looked at nothing. Whether the Streamlit surface gets a gate of
    its own is a separate product call and is not decided here.
  - The entry point audits **every** file it is handed. It read `argv[0]` and
    dropped the rest, while the `pa11y` branch beside it in the same `make`
    recipe takes a list — the two halves disagreed about their own arity.

- **`diff --json` becomes a versioned document, and a refused diff becomes a
  readable one (part of #121).** `lavender diff` had a `--json` flag that printed
  `RunDiff.to_dict()` straight out: no `schema_version`, no committed schema, and
  — the part that mattered — **nothing at all on stdout when the diff was
  refused**. Measured on the tree before this change: both refusal paths exited
  `2` having written zero bytes to stdout and a prose sentence to stderr, so
  `lavender diff --json | jq` received an empty stream. Every other `--json`
  surface in this CLI promises the opposite in its own `--help` text ("a refusal
  is emitted in the same shape, so a script cannot read one as an empty
  result"); `diff` was the one command that did not keep it, and its flag was
  hand-rolled rather than going through `_add_json_flag`, which is how the
  promise and the behaviour drifted apart unnoticed.
  - The success document is the same keys at the same depth — `shifts`,
    `unchanged`, `entered`, `left` are exactly where they were — plus
    `schema_version`, `command` and `ok`. Nothing that already parsed this
    output stops parsing.
  - **The two refusals are given different kinds, because they have different
    next moves.** A run id that resolves to nothing is `not_found` and the caller
    should run `lavender runs list`; two runs that answer different questions is
    `invalid_input` and the caller should pass `--allow-mixed`. One kind for both
    would report "something was wrong" and hide which of the two it was.
  - `schemas/diff.schema.json` is committed. `coverage_delta` and
    `exposure_delta` are keyed by whatever the two manifests recorded, so the
    schema can only bound them as objects and says so in its own description
    rather than looking stricter than it is; the constraint it cannot carry —
    every value is a number or `null`, never a string, and `null` means one side
    did not record the figure — is a test.

- **`--json` on `corrections` and `pending-corrections`, with committed schemas
  (part of #121).** The two ledgers read back one layer earlier than
  `recommend --json`: what a person asserted and cited, and what they have asked
  an upstream source to change and not reconciled yet. Every correction row
  carries a citation by construction, so the ledger has the same
  sourced-or-nothing shape the recommendation document publishes.
  - **A listing and a write are different runs, and the document says which.**
    `action` is `list` or `record` (`list` or `file` for pending corrections). A
    listing fills `count` and the rows; a write leaves both `null` rather than `0`
    and `[]`, because a zero immediately after a write would tell a script the
    ledger is empty in the same breath as telling it a row landed. An empty
    *listing* is `0` and `[]` — that one is a measured emptiness.
  - **A write never echoes the asserted value back.** An identity value is the one
    thing this project promises never leaves the machine it was typed on, and JSON
    is the output most likely to be piped into a log. The console path already
    withheld it on the refusal branch; the document withholds it on the success
    branch too, and a test asserts the string is absent from the whole document.
  - `edit_url`, `superseded_by_value` and `superseded_at` are `null`, never `""`:
    "no edit route is known for that source kind" and "the edit route is the empty
    string" are different statements, and only the first is one this project can
    make.
  - The `corrections` refusals keep their published exit code of `1` rather than
    inheriting `_refuse`'s `2`. Adding an output format is not a reason to move an
    exit status a caller's script already branches on; the `error` document carries
    the machine-switchable reason instead.
  - `test_every_published_schema_pins_a_version_of_its_own` is new beside the
    written-out version list: the list makes adding a document deliberate, and the
    new test catches a schema that shipped pinning somebody else's version.

- **`--json` on `recommend`, `export` and `doctor`, with committed schemas (part of
  #121).** One versioned document per invocation, described by
  `schemas/*.schema.json`, generated from `pipeline/jsonout.py` rather than
  hand-written beside it. The point is not convenience. Three of this project's
  guarantees are asserted today by tests over Python objects, which means a third
  party has to take them on trust; these documents make them checkable from a
  shell.
  - **Identity is never inferred, structurally.** The `recommend` schema pins
    `inferred` to `false`, has no slot for a guessed value, and makes a sourced
    gender without provenance a schema error. A pick whose basis is `unknown`
    carries `sourced_gender: "unknown"` — a value, not a missing key — and no
    citations. A property test drives the demo world across k, both lenses, the
    whole `--lens`/`--explore` range and the men filter, and asserts no pick ever
    carries an unsourced label; its checker has a negative control of its own,
    because a guard that cannot see the defect it guards against reads as a pass
    on every run.
  - **`doctor --json` publishes the egress allowlist.** The modules permitted to
    open a socket moved out of `tests/test_privacy.py` and into
    `pipeline/doctor.py` as `NETWORK_EGRESS_MODULES`; the test imports it, so
    there is still one definition and the same gate, but what this tool may
    contact is now readable without opening its test suite. The document also
    reports `upstream_checked`, because a run without `--check-upstream` carries
    no upstream checks at all and their absence is not a report that every API
    was reachable.
  - **`export --json` wraps the portable file verbatim** and adds only metadata
    (format, count, generated-at, destination), so the envelope cannot carry a
    field the chosen format does not already carry.
  - **A refusal is emitted in the same shape**, with a machine-switchable
    `error.kind` and a non-zero exit, so a script cannot read a refusal as an
    empty result. An unmeasurable share is `null`, never `0.0`.
  - `recommend --json` carries no timestamp and no run id, and is byte-identical
    across runs of the same query. The guard runs the CLI in three separate
    interpreters under different `PYTHONHASHSEED` values over a seven-pick world,
    because two renders inside one interpreter share a stable set iteration order
    and a one-row fixture has no order to get wrong.
  - Per-document schema versions, not one shared number: bumping one would tell
    every consumer their contract had changed when only one document moved.
  - `python3 scripts/gen_schemas.py [--check]` writes or verifies the committed
    schemas, and the validator in `pipeline/jsonout.py` is dependency-free and
    asserts its own keyword coverage against those schemas, so it cannot pass a
    document by ignoring a constraint it does not implement.
  - **Not yet:** `report`, `feedback`, `refresh`, `corrections`,
    `pending-corrections`, `eval` and `runs`. #121 stays open for them; `diff`
    already had its own `--json` before this.

- **Identity-blind tag and era filters on `recommend`, `report` and `export` (#123).**
  `--include-tags`, `--exclude-tags`, `--year-from`, `--year-to`. They narrow the *candidate
  pool* before anything is scored, so the values lens, rank protection, the serendipity pass
  and both counterfactual ranks all run over what survives — which keeps each card's
  rank-shift sentence a claim about the lens rather than about three mechanisms at once
  (#113). Every filtered run states the active filters: on stdout for `recommend`, as a
  `<p class="filters">` in the report, and on **stderr** for `export`, because the playlist
  file is contracted to carry artist and track names and nothing else.

  **Absence never excludes.** An artist with no tags, or no known start year, is kept by all
  four filters. This is the same rule that governs identity, and it matters for the same
  reason: upstream tag and date coverage is thinnest for the least documented artists —
  small, new, independent — so a filter that dropped them for having no metadata would
  quietly re-impose the popularity bias the whole ranking resists, while looking like a
  neutral content preference. A filter removes only on a *positive* match.

  **Identity-blindness is guarded twice.** The predicates live in a new
  `recommender/content_filters.py` rather than in `recommender/filters.py`, because that
  module holds `is_sourced_man_only`, which reads a sourced gender on purpose; keeping them
  apart is what lets the new module be held to the whole-module AST scan `diversify.py`
  already lives under, instead of a weaker per-function allowlist. The module also imports
  no project type at all and is never handed an `Artist` — the two values it sees are passed
  in — and a second test asserts that. Beside the code guard, a property test computes the
  surviving set two ways and asserts the identity mix is identical, so blindness is checked
  at the outcome as well as at the source.

  A contradictory specification is refused rather than silently decided: a tag both included
  and excluded, or `--year-from` later than `--year-to`, exits 2 with the reason. A filter
  that matches nothing is an empty result with a sentence saying so, not an error.

- **`Artist.career_start_year`, sourced from the MusicBrainz lookup already performed.**
  Parsed from `life-span.begin` by `parse_musicbrainz_life_span_begin`, via a second,
  *optional* `CareerSpanSource` protocol so no existing enricher breaks: one that does not
  implement it yields unknown, which is the answer the era filter keeps. **It is the act's
  begin year, not the year of its first release** — the field and its docstring say so,
  because a first-release year needs a release-group payload nothing here retrieves, and
  labelling a formation year as a release year would be a number wearing a name it did not
  earn. Every unreadable upstream value, and every malformed value already in a cache,
  decodes to `None`; a coerced garbage year would silently drop real artists from a filtered
  run. No cache migration: a payload written before the field existed decodes to unknown,
  which is what this build actually knows about those artists.

  `--min-tag-weight`, which #123 also proposed, is deliberately **not** implemented: tags
  carry no weight anywhere in this codebase (`content.py` builds a candidate's vector with
  `dict.fromkeys(artist.tags, 1.0)`), so the flag would have had to invent the number it
  filtered on.

### Fixed

- **The interactive dashboard crashed on the demo world.** `segment_retention` started reporting
  `None` for a segment absent from pure taste's top-k (#129) and `app/render.py` was taught to
  say so; `app/dashboard.py` was not. Its retention table still did `f"{...:.0%}"` over those
  values, so the Streamlit app raised
  `TypeError: unsupported format string passed to NoneType.__format__` — on the demo world, which
  is the only world it shows, because that catalogue holds no artist sourced as `Gender.OTHER`
  and so `other_retention` is `None` for every lens.

  **Two gates were in a position to catch it and neither could.** `mypy --strict` read a
  `cast("dict[str, float]", ...)` on the same expression, and a cast is a promise it takes at its
  word. And `tests/test_committed_render.py` holds the *static* render to the byte while
  `tests/test_observability.py` holds the *panel* — between them, the dashboard's own table
  construction was rendered by nothing. It is now built by two named functions
  (`fairness_exposure_table`, `fairness_retention_table`) and
  `tests/test_dashboard_fairness_tables.py` calls them over the real demo world.

- **An empty top-k reported a 0% share for every identity segment.** `exposure_at_k` divided by
  the slot count and fell back to `0.0` when there were no slots. Seven shares of zero is not a
  distribution — over any real top-k they are a partition and sum to one — so what read as
  "no segment held any of the slots" was "there were no slots". It now reports `None` per
  segment, and the fairness panel says "not measured" in both the static render and the
  dashboard. Same rule and same reason as #129; the third figure in that family.

  The test that pinned the old behaviour, `test_exposure_at_k_empty_is_all_zero`, is rewritten.
- **An empty run reported "0% sourced" instead of no share at all.**
  `IdentityCoverage.sourced_fraction` and `unknown_fraction` divided by `total` and fell back to
  `0.0` when `total` was zero, and `to_dict()` published both. Over a run with no picks that
  reads as a measurement — "none of these picks carried a sourced identity" — when what happened
  is that there were no picks. The tell is the arithmetic: over any real run the two shares are a
  partition and sum to one, and the empty case returned `0.0` and `0.0`, which sums to nothing.

  Both now return `None` over an empty run, `to_dict()` publishes JSON `null` beside a new
  `fractions_measured` flag, and `total` is the denominator that says why. This is the same rule
  and the same reason as `segment_retention` reporting `null` for a segment absent from pure
  taste's top-k (#129) — a different site of one defect, not a new one. `summary_line()` was
  already honest ("No picks yet.") and is unchanged, so no rendered page moves.

  **The test that pinned the old behaviour is rewritten.** It asserted
  `sourced_fraction == 0.0 and unknown_fraction == 0.0`, which describes a run where no pick was
  sourced *and* no pick was unknown — a distribution that cannot exist. Two tests replace it: one
  for the empty run's nulls, one asserting the two shares sum to one whenever they are measured.

- **A merge-blocking fairness guarantee was measured over an empty segment, and
  scored it 1.0.** `segment_retention()` returned `1.0` when the segment it protects
  had no artist in pure taste's top-k — a perfect retention score for a measurement
  that never happened. The demo fixture world holds **no** artist sourced as
  `Gender.OTHER`, so `other_retention_all_lenses` has been passing over an empty set:
  the guarantee added by #68 *because* the lens shipped a harms note nothing verified
  was itself verifying nothing, and the dashboard rendered it to a reader as `100%`.
  `unknown_retention` is unaffected — the demo world does hold an unknown-identity
  artist, so that number was always real.

  Retention for an absent segment is now `null`, not `1.0`; `rank_shift_by_segment()`
  likewise reports `null` rather than `0.0`, which read as "measured, and it did not
  move". The report gained `unknown_retention_measured` / `other_retention_measured`
  and the `*_base_count` denominators that let a reader tell a real pass from an empty
  one, `min_*_retention` is `null` when nothing was measured, and `lavender eval`
  prints an `UNMEASURED:` line naming the guarantee and the empty count. The run is
  not failed by it: whether the eval world should contain such an artist is a decision
  about a real person's sourced identity, not one a gate can make.

  `tests/test_exposure.py::test_retention_is_one_when_no_unknown_artists_present`
  asserted the old behaviour as intended — it pinned the defect — and has been
  rewritten alongside four tests covering the measured case, the report's
  measured/unmeasured pairing, and the rank-shift null. The dashboard renders an
  unmeasured cell as words rather than a percentage. Every change was checked by
  negative control.

### Added
- `lavender ingest --from-file` — build a listening profile from an export the listener already
  has (`pipeline/fileingest.py`): a Last.fm data download (CSV, or the `user.getrecenttracks`
  JSON), Spotify's "Extended streaming history" JSON, a ListenBrainz export, or a plain
  `artist,track,timestamp` CSV. No API key; **no socket at all** unless `--enrich` is passed, which
  resolves identity against MusicBrainz/Wikidata and nothing else. Until now the only route to a
  real profile was a live Last.fm key, so someone who left Last.fm, listens on Spotify, or will not
  create a developer account could not run this on their own history — and a file import is the
  most local-first ingest there is: the listens never leave the machine. Every format is read under
  a documented contract that is checked rather than assumed: a missing required column fails naming
  the column, malformed rows are counted and reported (never silently dropped — a half-read file
  that reports success is this project's dominant defect class in a new place), `--format auto`
  refuses to guess rather than importing under the wrong contract, an unreadable timestamp is never
  replaced with "now", and a re-import adds no rows. An export carries plays and not Last.fm's tags
  or similar-artist graph, so `ImportedHistory` returns empty for both and says so at both surfaces
  instead of inferring either from track names. Closes #119.
- Periodic re-enrichment is scheduled (ADR 0013). `make refresh LAVENDER_USER=<you>` is the
  bounded re-check; `make schedule LAVENDER_USER=<you>` prints the launchd agent (macOS) or
  crontab line that runs it every 7 days on the operator's own machine, with absolute paths, a
  log, and no credential in the entry — it sources a mode-600 env file instead. Deliberately not
  a GitHub Actions cron: the cache being refreshed is a personal listening history in a per-user
  data directory, so a hosted runner has nothing to refresh unless that history is uploaded to
  CI. Closes the "there is no scheduler" gap `docs/ROADMAP.md` §11, `docs/RESEARCH-ROADMAP.md`
  R2/E9, and AIR-8 all recorded.
- Docs-figures gate + auto-stamp (`scripts/docs_figures.py`, `make stamp`): one manifest pairs
  each figure the docs state about this repo with the callable that re-derives it, so gating a
  new claim is a manifest row instead of another bespoke script. Nine rows ship across
  `README.md`, `CONTRIBUTING.md`, `DEFINITION_OF_DONE.md`, and `docs/ROADMAP.md` §7, covering the
  test count, coverage total, coverage floor and measured scope, `lavender refresh --limit`'s
  default, and the mutation kill threshold. `--write` stamps the derived value in rather than
  asking a human to retype it. This is the "auto-stamp backlog item" the 2026-08-04 docs-currency
  guard recorded as the systemic fix and left open.
- `lavender refresh --user <name>` re-asks MusicBrainz/Wikidata about artists
  already in the cache, closing the upstream-correction round-trip: a filed
  pending correction can now be reconciled against an observation, which the
  demo-only refresh could never do. Bounded by `--limit`/`--artist` against a
  ~1 req/s upstream, and resumable through the HTTP cache.

### Removed
- `scripts/check-readme-claims.py`, superseded by `scripts/docs_figures.py`. Its two claims are
  the first two rows of the new manifest and are checked identically; its pure-logic unit tests
  moved to `tests/test_doc_figures.py`. No gate was weakened or dropped.

### Fixed
- **The dashboard's fairness observability panel was recomputed without `explore`, so it published
  exposure shares for a ranking the page was not showing.** The displayed list carried the
  Serendipity slider's value and the `recs_by_lens` sweep beside it did not; every other argument
  matched, so it was an omission, not a different question by design. At the top of the slider the
  page reported that no sourced man held a top-3 slot while a sourced man sat visibly in slot 3 of
  the same screen, and `identity_coverage` immediately above was computed on the displayed list —
  two adjacent panels describing two different rankings. `app/observability.py` now returns the
  list to display and the panel about it from one call, so a surface cannot show one ranking and
  measure another; `app/dashboard.py`, `app/build_static.py` and `lavender report` all use it.
  Neither of the other two exposes an `explore` control, so their output is unchanged. Tested
  through the new seam because `app/dashboard.py` is excluded from coverage.
- **EXP-04's excellence bar claimed identity-segment exposure is "statistically unchanged by the
  diversifier at any setting".** Measured at k=3 on the demo world, it is not — moving the slider
  changes which segment holds a top-3 slot. `docs/ideation/03-expansions.md` now records that as an
  aspiration with the measurement, and a test pins the fact so the claim cannot be quietly
  restored. Identity-*blindness* is unaffected and still proved by AST.
- **The why-card's rank-shift sentence attributed serendipity and the `--hide-sourced-men`
  subtraction to the values lens, including at `--lens 0` where every `rerank_delta` in the run is
  `0.0`.** `base_rank` was recorded before the lens and `rank` was not assigned until after two
  further mechanisms had run, so `rank - base_rank` absorbed all three causes while the sentence
  named only the first. A listener who raised the identity-blind Serendipity slider was told the
  identity lens had promoted a sourced man four places and demoted a woman-fronted band and a
  nonbinary artist; with the filter on, an unknown-identity pick rendered a lens-caused improvement
  it had not received, which `docs/ROADMAP.md` states is test-asserted never to happen. The false
  sentence reached the CLI, the dashboard, the committed render, and every exported playlist
  annotation. `Recommendation` now carries `lens_rank`, stamped between `rerank` and the
  serendipity pass, and `rank_shift_statement` reads `base_rank -> lens_rank` — the lens and
  nothing else. `tests/test_why.py`'s two guards are parametrised over `explore` and
  `hide_sourced_men` rather than testing only the defaults, which is why they could not see this.
  `recommender/hybrid.py`'s module docstring and the filter comment, both of which asserted the
  invariant being violated, are corrected.
- **A locally filed correction counted as upstream speaking, so a dead upstream erased the
  citation it never re-read.** `enrich_artist` is called with the cache on the refresh path, so
  `Cache.get_corrections` merges the operator's ledger into the evidence and the resolvers turn it
  into ordinary `Source` rows. `_is_sourced` asked only whether *any* source existed, so a
  corrected artist scored as "upstream answered" on a run where every lookup timed out: the cached
  MusicBrainz/Wikidata citations were written over, `fetched_at` advanced, `diff_identity_sources`
  walked the new sources and had nothing to report, `RefreshOutcome.upstream_answered` was `True`
  against its own docstring, and `lavender refresh --user` exited `0` and reconciled pending
  corrections against an upstream nobody read. The per-axis guard added for #96 was defeated the
  same way — a correction is on its axis on every run, so that axis was never "empty". The refresh
  path now reads the discriminator the model already carried
  (`Source.is_local_correction`): proof of an answer means an *upstream* citation, on `_is_sourced`
  and on every per-axis guard. Nothing is lost — a correction lives in the ledger, not in the
  cached row, and is re-applied and still wins on priority at the next enrichment.
- **`DEFINITION_OF_DONE.md` said coverage is measured on `pipeline`/`recommender`/`export`.**
  `app` joined the coverage addopts on 2026-08-28 and the sentence did not follow. Found by the
  new docs-figures gate on its first run against `main`, and stamped from the addopts.
- **The committed branch ruleset carried no bypass actors, and ADR 0001 argued that
  emptiness was the stricter posture.** It is the lockout. GitHub rulesets do not
  implicitly exempt admins, so applying `docs/audits/branch-ruleset.json` as committed
  would have left `main` with no break-glass path — no merge, no push, and no way to
  edit the ruleset doing the blocking without the access it had just removed — and
  GitHub answers such an apply with a `201`. The file now carries the owner's standing
  admin bypass `{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}`,
  `tests/test_branch_ruleset.py` fails on the empty list and on the four neighbouring
  ways to lose it (including the subtler `bypass_mode: "pull_request"`), and ADR 0001
  carries a dated correction rather than a silent edit. Four documents that called the
  ruleset "proposed — not yet applied live" are corrected against the live API read;
  `CONTRIBUTING.md` no longer advertises "no admin bypass".

- A refresh can no longer erase a cited identity when nothing answers. The
  enricher reports an upstream failure as "no evidence", which is byte-identical
  to "upstream holds no claim"; on the refresh path that would have written an
  evidence-free artist over every sourced one and reported **zero changes** while
  doing it, since the source diff walks the *new* sources. Only an artist that
  comes back carrying sources is written; anything else keeps its label and its
  original `fetched_at` lineage date, is named in the report, and leaves
  `upstream_queried` false so no correction is reconciled against an upstream
  nobody read. A wholly silent run now exits non-zero instead of zero.

- Release authorization now runs from reviewed `main` through the immutable
  portfolio authorizer, builds and signs the exact selected commit, and hands
  only verified assets to a checkout-free publisher that rechecks the tag
  object immediately before creating the release.

### Security
- Update the transitive GitPython lock, 3.1.58 -> 3.1.61, clearing PYSEC-2026-3785/3786/3787/3788
  (fixed upstream in 3.1.59). GitPython arrives through `streamlit` in the `app` extra. The four
  advisories had `make security` — and therefore the whole ordered `verify` pipeline, so a11y, eval
  and i18n never ran at all — red on `main` and on every branch cut from it. Fixed by regenerating
  the lockfile (`uv lock --upgrade-package gitpython`), not by waiving: `AUDIT_IGNORES` stays empty
  and `pip-audit` is back to "No known vulnerabilities found" with no `--ignore-vuln` flags.
  Separately committed rather than folded into the PR that surfaced it, same as the 3.1.50 -> 3.1.55
  and 3.1.55 -> 3.1.58 bumps below.
- Update the transitive GitPython lock from 3.1.50 to 3.1.55, clearing the
  high-severity joined-short-option clone bypass fixed after 3.1.50.
- Update the transitive GitPython lock again, 3.1.55 -> 3.1.58, clearing two advisories
  disclosed after the prior bump (GHSA-3f7w-8rr8-f37f, GHSA-p538-c434-8v24; fixed upstream in
  3.1.56/3.1.57 respectively). Found via `pip-audit` while verifying an unrelated PR; fixed here
  as a minimal, separately-committed companion change rather than folded into that PR's diff.
- Update the transitive `pip` lock, 26.1.2 -> 26.2.1, clearing PYSEC-2026-3721 (CVSS 6.5, fixed
  upstream in 26.2). `pip` is pulled in by `pip-api`, which `pip-audit` depends on — the auditor's
  own dependency tree is what tripped the auditor. It had the whole merge-blocking gate set red on
  both Python 3.12 and 3.13 from the 2026-08-24 scheduled run onward: `make security` exits 1, and
  `verify` runs its stages in order, so a11y, eval and i18n never ran at all while it was broken.
  Fixed by regenerating the lockfile (`uv lock --upgrade-package pip`), not by waiving —
  `AUDIT_IGNORES` stays empty and `pip-audit` is back to 0 findings with no `--ignore-vuln` flags,
  so RR-1's "re-open only if a new pip advisory appears" clause needs no new accepted-risk row.

### Fixed
- **A backing vocalist is no longer treated as fronting the band.** `is_fronting_role` matched the
  substring `vocal`, so "background vocals" (MusicBrainz) and "Backing Vocals" (Discogs) both read
  as fronting — and front-people are what `BandComposition.female_fronted` is derived from, so a
  woman singing harmonies would have made a band "female-fronted". Exactly the over-claiming the
  guardrails exist to prevent, shipped by the change that added live lineup enrichment. A test
  asserted the wrong behaviour (`("Backing Vocals", True)`); it now pins the right one, alongside
  "additional"/"guest"/"session" vocal credits.

### Added
- **`scripts/upstream_worklist.py`** — turns a local cache into a prioritised list of MusicBrainz
  edits, offline and credential-free. "Fix it at the source" was the stated posture
  (`CONTRIBUTING.md`) with no way to see *what* was missing or which gap was worth an evening.

  It separates the categories because they are different work with different care requirements —
  and the split is the point. On a 7,146-artist library, **593 of 785 gaps are bands whose lineup
  MusicBrainz already has, missing only a fronting role attribute on an existing member relation**:
  ordinary discography work, no identity claim, no citation dilemma. Only 57 are gender fields on
  people, where the rule is the project's own no-inference guardrail pointed outward — publicly
  self-identified and citable, or correctly left empty. Ranking is by the operator's play count,
  because fixing a band they play constantly improves the *candidate graph*, not just one label.

  The generated report is gitignored: it is derived from a personal listening history.

### Changed
- **Renamed to Lavender Rotation** ([ADR 0012](docs/adr/0012-rename-to-lavender-rotation.md)) —
  the old name described a scope [ADR 0011](docs/adr/0011-queer-lens-and-the-trans-vocabulary-amendment.md)
  had moved. Complete rather than cosmetic: repo, CLI (`wad` → `lavender`), env vars (`WAD_*` →
  `LAVENDER_*`), log namespace, and data directory. The `wad` console script and `WAD_DATA_DIR`
  keep working, deprecated, until the first tagged release.

  **The data directory migrates rather than resetting.** A cache is hours of rate-limited upstream
  fetching — 95,613 scrobbles and 450 enriched artists on the maintainer's machine — and losing it
  to a rename would also mean re-asking MusicBrainz for what it had already given us.
  `migrate_legacy_data_dir()` moves it under four conditions that make that safe: only when the new
  directory does not exist, only when the old one does, never when an env var names a path
  explicitly, and by a same-filesystem rename that is atomic and reversible. A failed move degrades
  to a working empty directory instead of crashing at startup.

  The name carries the mechanism, not the dataset: a repo named for a category of people would
  advertise the artifact `identity-data-ethics.md` exists to prevent, and would put that category
  in every browser history and CI log that touches it.

### Added
- **A queer lens, and the guardrail amendment it required** — `--lens-name queer` boosts sourced
  queer women and sourced nonbinary artists, alongside the unchanged default lens. Recorded as
  [ADR 0011](docs/adr/0011-queer-lens-and-the-trans-vocabulary-amendment.md), which **amends a
  guardrail `CLAUDE.md` and `README.md` both marked binding** and should be read before touching
  either identity axis.

  "Queer" is not a new `Gender` value; it is a second sourced axis (`QueerIdentity`): an
  `Orientation` from Wikidata P91 or a cited statement, plus a trans self-identification. Two
  properties do the safety work. The axis is **read, not collected** — the raw value each source
  asserted has always been stored for provenance, so a P21 claim of `Q1052281` (*trans woman*) was
  already in every cache this project ever wrote; P91 comes out of the entity document already
  fetched for P21, so the request count is unchanged and nobody is asked anything new. And
  `trans_self_identified` is **tri-state, never `False`** — the model refuses to express "this
  person is not trans", exactly as `female_fronted` refuses to express "not female-fronted".
  `Gender` itself is untouched: a trans woman is still `Gender.WOMAN`, with no cis/trans
  distinction in the gender vocabulary.

  Scope decisions, all stated rather than assumed: nonbinary artists align on gender alone (no
  second, rarer disclosure demanded of the least-documented group); sourced men are out of scope
  by design and lose nothing by it; asexuality and demisexuality are recorded and not boosted, on
  the `Gender.OTHER` precedent, because whether the ace spectrum sits under a queer lens is
  contested among ace people and answering it silently would speak for them. Every Wikidata QID in
  the vocabulary was verified against live Wikidata rather than guessed — `Q43455`, the obvious
  guess for "queer", is *ethnology*.

  `privacy-notes.md` can no longer claim no special-category data is stored, and now says so:
  orientation is GDPR Art. 9 outright. The honest cost is recorded in the ADR — "this repo cannot
  produce a list of who is trans" used to be true of the type system, and is now true only of the
  process.
- **`--hide-sourced-men`, an opt-in output filter** (`recommender/filters.py`), on
  `recommend`/`report`/`export`. The values lens is boost-only and bounded, so a sourced man with a
  high enough taste score survives it at any strength — that is what boost-only means, not a
  defect, and `LensSpec.harms_note` now says so. A listener who wants them gone is asking for a
  different mechanism, and it is kept in a separate module for that reason: a filter is the only
  thing here that can make an artist disappear.

  It removes only a *positive* sourced claim — an artist sourced as a man, or an act whose sourced
  fronting lineup is entirely sourced men (so a band fronted by a sourced woman *and* a sourced man
  is kept). The obvious implementation, keeping only what the lens boosts, was rejected: it deletes
  every unknown artist, since `values_aligned` is `False` for an absent claim exactly as for a
  man's. On the history this was written against that would have been 4 of 10 picks and 57 of 88
  catalogued artists — not men, but artists nobody has sourced, disproportionately the
  less-documented ones. Off by default, so the eval and every existing caller are unaffected, and
  applied after ranking so each surviving pick's "the lens moved this from #19 to #7" still refers
  to the real pure-taste ordering rather than a counterfactual over a pre-filtered world.
- **Live username-to-recommendation orchestration (FIX-01)** — the deferral the README's
  "Project status" and the roadmap ledger both carried. `wad ingest --user <you>` syncs a real
  Last.fm history (incremental, resumable, unchanged fetch semantics), resolves identity against
  MusicBrainz and, where the record links to it, Wikidata's P21 claim, and enriches the candidates
  reachable from that taste; `--user` on `recommend`/`report`/`export` then reads that cached
  world back. Four parts made it work:
  - `MusicBrainzEnricher` (`pipeline/enrich.py`) — the first concrete `EnrichmentSource` besides
    the fixture one. It takes its fetcher as a constructor argument, so the entire parse → resolve
    → label chain is unit-gated offline against recorded payloads (`tests/test_live_enrichment.py`)
    and the suite still opens no socket.
  - `pipeline/http.py` — one rate-limited (1 req/s), cache-first, `User-Agent`-bearing transport
    for identity sources, added to the egress allowlist in the same change as the code that uses
    it, per the registry's own instructions (`docs/audits/privacy-notes.md`).
  - Candidate discovery (`pipeline.ingest.discover_candidates`). Enriching only the artists someone
    already plays cannot produce a recommendation — `recommend()` excludes known artists by
    construction, so a catalog built from a listening history alone yields an empty list. This is
    the step the demo world got for free from its fixture catalog.
  - `pipeline.lastfm.artist_query` — Last.fm rejects an `mbid=` parameter that is not one with a
    400. Since `parse_recent_tracks` falls back to the artist *name* whenever Last.fm supplies no
    MBID (a large share of a real history), every such artist previously turned `artist.gettoptags`
    into a failed request that aborted the whole ingest.

  **Resilience, learned the hard way on the first real run.** `ingest()` re-raised on any
  enrichment failure, so a single `ReadTimeout` on one artist's `artist.gettoptags` discarded a
  nine-minute ingest of 95k scrobbles and 48 enriched artists. A failed artist is now skipped and
  counted, not fatal — it stays in the profile, so it is still excluded from recommendation, and
  contributes no tags. Both HTTP paths also retry once, and only for a request that never got an
  answer (`is_transient_failure`): a 4xx is an answer, and re-sending it spends a rate-limit slot
  to hear it again. The retry matters more on the identity path, where the failure is *quiet* — a
  timed-out lookup leaves an artist `unknown`, indistinguishable in the output from an artist
  upstream genuinely has no claim about.

  Two limits are deliberate and documented rather than papered over. **Entity resolution is gated
  like the guardrail it is:** a name resolves only when exactly one MusicBrainz record matches it
  exactly, because attaching a stranger's sourced gender to an artist is the harm the no-inference
  rule exists to prevent, arriving by a different road. **An upstream failure resolves to
  `unknown`,** not to an exception and not to a retry-until-something-answers: a label here is
  either sourced or absent. Both mean a real history produces more `unknown` artists than the demo
  world does — which costs them nothing, unknown being first-class in the ranking.
- Verified-subject ledger for every identity citation the demo ships
  (`tests/test_demo_citations.py`): each citation is pinned to the subject a human read off the
  live registry, with the date. `citation_problem()` can only check that an identifier is
  well-formed; it cannot check that the record is the artist it was cited for, which is how three
  wrong Wikidata entities shipped. A citation absent from the ledger now fails, so an unverified
  identifier cannot be added silently, and the six `example.org` placeholders that stand in for
  real people's self-identification are enumerated rather than invisible.
- `wad doctor` now reports the on-disk cache size (`cache_size`, informational — never fails the
  run) alongside the existing `cache_path`/`cache_schema_version` checks: the one signal the CLI
  gives an operator for when to reach for `wad refresh --ttl-days`, since nothing previously
  reported cache footprint.
- Docs-currency guard (`scripts/check-readme-claims.py`, wired into `make test`): re-derives the
  actual test count (`pytest --collect-only`) and coverage total (`coverage report
  --format=total`) and fails if either drifts from README's "Project status" claim, instead of
  that sentence silently going stale as the suite grows. Closes the "M8 auto-stamp backlog item"
  PR #49 flagged as open ("the README build-blockquote test count is not bumped here"); same
  "claims must be regenerable, never hand-typed" discipline `scripts/writeup-check.py` already
  applies to `docs/writeup/methods.md`.
- Make the internationalization row's N/A reason part of the machine-readable
  conformance status instead of leaving the table parser with a bare exemption.
- Mutation-testing gate on the safety-critical modules (CQ-47): `make mutation` runs cosmic-ray
  over `pipeline/identity.py` (no-inference) and `recommender/rerank.py` (boost-only), executing
  the full unit suite against every generated mutant, and fails if fewer than 70% are killed per
  module (`scripts/mutation-gate.sh`, `scripts/mutation/*.toml`). Runs weekly + on demand in CI
  (`.github/workflows/mutation.yml`) rather than nightly — a deliberate lean-Actions trade,
  documented in the workflow. The first run measured identity at only 62.3% killed **despite
  100% branch coverage** — exactly the coverage-vs-assertion-strength gap CQ-47 names — so this
  change also hardens `tests/test_identity_model.py` with exact-semantics tests (priority order
  under conflict, per-kind confidence arithmetic, filter/guard paths). Measured after hardening:
  identity 107/122 killed (87.7%), rerank 43/44 (97.7%); the survivors are equivalent mutants
  (enum `is`→`==`, unreachable dict defaults, order-preserving sort-key transforms, no-op
  rounding widths) plus one weakened defensive `assert delta >= 0.0` whose condition never fires
  precisely because the boost-only invariant holds upstream.
- Browser-driven accessibility specs (`tests/test_e2e_a11y.py`, A11Y-02/07/08/09): Playwright
  drives real Chrome over the static render and asserts keyboard completeness (skip link first
  and working, every interactive element reached in DOM order, no trap, 3px focus visible),
  320 px reflow (no page-level horizontal scroll), and reduced-motion (the
  `prefers-reduced-motion` override ships and nothing animates in either preference state).
  They auto-skip locally without a Chrome/Chromium; CI sets `WAD_E2E_REQUIRE=1` so a missing
  browser fails there instead of silently weakening the gate. Lighthouse CI remains absent, and
  the manual screen-reader walkthrough sign-off (M5) remains pending and human-only.
- `wad --log-format json`: opt-in JSON log lines on stderr, carrying the same fields as the
  `key=value` default; logging remains stderr-only with no network sink either way. Makes the
  README Observability claim true — the flag was documented before it existed.
- Merge-blocking no-identity-in-logs gate (`tests/test_log_privacy.py`, OBS-11): behavioural and
  AST-scan proofs that no log call site emits identity vocabulary, extending the no-inference
  invariant into the log stream.
- Playlist export: push recommendations to a Spotify playlist (OAuth Authorization Code flow,
  env-only credentials) or download a portable, account-free track list (plain text / CSV / M3U /
  JSPF) (#1).
- Shared `WhyThisArtist` explanation object, reused by the dashboard, static a11y render, CLI, and
  export, so identity/why wording cannot drift between surfaces (#1).
- `CITATION.cff` (#4).
- `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (#7).
- i18n `N/A` declaration (`docs/I18N.md`) plus a merge-blocking enforcement gate
  (`scripts/i18n-gate.sh`, CI `i18n` stage) (#8).
- Renovate (`renovate.json`) with `minimumReleaseAge: 72 hours` and GitHub Actions digest pinning
  (BL-8, 56628ee).

### Changed
- Recorded the supported Python floor of `>=3.12` in the remaining developer-facing
  surfaces (ADR 0004, which supersedes ADR 0002 and ADR 0001's four-version matrix
  provision; `CONTRIBUTING.md`; the committed main-ruleset target now requires only
  the supported `verify (3.12)`/`verify (3.13)` contexts). The floor itself landed
  in `pyproject.toml` via #51; this closes out the documentation and ruleset trail
  from #42.
- Migrated the Python floor from 3.9 to `>=3.10` (#6). Unblocked every dependency fix gated to
  Python ≥3.10 (see Security, below) and dropped Python 3.9 (EOL 2025-10-31) from the CI matrix.

### Fixed
- Three of the demo's Wikidata identity citations pointed at the wrong records. Mitski cited
  `Q16735549` (Andreas Constantinou, a Cypriot footballer, whose own P21 is male), Phoebe Bridgers
  cited `Q28907802` (a douar in Morocco), and Lucy Dacus cited `Q47545178` (a politician). Each was
  a well-formed Q-number that resolved, so `citation_problem()`'s shape check and any link checker
  passed them. Corrected to `Q23761694`, `Q24883319` and `Q27967785`, each confirmed against the
  live Wikidata API to be the named artist with `P21=Q6581072`.
- The demo's two `DISCOGS_LINEUP` citations were bare slugs (`/artist/big-thief`,
  `/artist/boygenius`), 22 lines after the fixture's own comment forbids exactly that shape.
  Discogs addresses artists by numeric id; corrected to `/artist/5009441-Big-Thief` and
  `/artist/6774153-boygenius`, both confirmed against the Discogs API including the member lists
  the band-composition claim rests on.
- `citation_problem()` now validates registry addresses by **host** as well as by source kind, so a
  citation claiming musicbrainz.org, wikidata.org or discogs.com has to be an address that registry
  can resolve, whatever kind carries it. `DISCOGS_LINEUP` was previously exempt by design, and it
  is the only source kind behind the demo's band-composition claims. Citations that claim no
  registry stay free-form, which was the original reason for the exemption.
- The demo-citation gate walked individual identity and band composition but not the front-person
  identities nested inside composition, reaching one level short of the data it covers.
- The committed `docs/audits/dashboard.html` is gated against the renderer (#71). It is a
  publicly browsable deliverable that nothing regenerated and nothing checked: `make a11y` ran
  `python -m app.build_static`, which **overwrote** the file and then audited the fresh copy, so
  the gate destroyed the evidence before looking, and `tests/test_e2e_a11y.py` builds into a
  tmpdir. On `main` the page still displayed three MusicBrainz citations that locate no record and
  their matching `/edit` links — the exact defect PR #66 fixed, on public display months later.
  `tests/test_committed_render.py` now asserts byte-equality with a fresh render (stage 3, before
  the a11y stage can overwrite anything), that the render is deterministic, and that every citation
  displayed locates a record; both new assertions were verified to fail against `main`'s copy.
  Regeneration moved out of `make a11y` into an explicit `make render` (also wired into
  `make audit`).
- Narrowed `[tool.coverage.run] omit` from `app/*` to `app/dashboard.py` (#71's related finding).
  The old justification — "Streamlit UI — verified via a11y + manual walkthrough" — held for
  neither: the a11y spec excludes the Streamlit dashboard in its own words and the walkthrough is
  still pending. It also hid three modules that are ordinary logic and measure 82–100%. Total
  coverage is unchanged at 97%; `app/dashboard.py`'s 0% is now recorded as an open gap in
  `docs/audits/accessibility-2026-07-17.md` instead of described as verified.
- `wad refresh` no longer deletes a filed pending correction when nothing upstream changed (#70).
  `corrections.reconcile` matched on `(artist_id, source_kind)` alone and never read
  `proposed_value`, while `ingest._diff_sources` emits a change when *either* the asserted value or
  the retrieval date moves — so a pure date refresh silently removed a person's note and printed
  "reconciled 1 pending upstream correction(s)". Reconciliation now requires the observed value to
  be the proposed one, compared through the controlled vocabulary (`identity.normalise_asserted_value`,
  so `"female"` reconciles a `"woman"` proposal); date-only changes reconcile nothing; a change to
  any other value marks the row **superseded** and keeps it on file with what upstream now asserts;
  and reconciliation only runs when an upstream source was actually queried, so the demo-only
  command reports `reconciled 0 … no upstream identity source was queried` instead of claiming an
  upstream that was never contacted. `reconcile` now returns a `ReconcileOutcome` naming every row
  it reconciled, superseded, or left open, and writes the operator summary itself — `pipeline/cli.py`
  is coverage-omitted as thin argparse glue, which is where the wrong status line was hiding.
  `tests/test_pending_corrections.py::test_reconcile_drops_matching_row_and_keeps_others`, which
  asserted the deletion was correct using a change whose old and new values were identical, is
  replaced by absence-of-harm tests plus an end-to-end CLI regression.
- The no-inference AST scan now covers the code that could infer (#72). It walked a hardcoded list
  of four function names in one file; `pipeline/identity.py` defines seven, nothing asserted the
  list covered the module, and a helper mapping genre tags to a gender — called from
  `resolve_identity`'s unknown fallback — passed it. The scan now walks every `def` **and
  `async def`** in every `pipeline/*.py` module, with no allowlist to fall off. Functions that
  legitimately handle content tags are named in `TAG_HANDLING_EXEMPTIONS` with a reason (a stale
  entry fails, so a hole cannot be pre-drilled) and are held to a stricter taint check: no value
  derived from a forbidden read may reach an identity constructor. The scan is extracted so it can
  be pointed at a synthetic bypass module and asserted to **fail** — a guard never shown failing is
  not evidence. `recommender/`, `app/`, and `export/` are now asserted to construct no identity
  objects, so an inference path cannot be introduced by relocating it. And
  `pipeline.identity.assert_permitted_only`, which had no caller outside its own test, is now
  invoked at the top of `resolve_identity` and `resolve_composition`, with a test proving it fires
  on the running path rather than only that its `if` works.
- The values lens's published harms note and the ranking now agree (#68). The note is rendered to
  the reader and promised that anyone unaligned was "never down-ranked, never treated worse than
  an unknown-identity artist"; the re-rank pinned only *unknown* slots, so an artist sourced as
  `Gender.OTHER` could be ranked below a **lower-scoring** unknown artist. Sourced `Gender.OTHER`
  is now rank-protected alongside `UNKNOWN` (`recommender/rerank.py::RANK_PROTECTED_GENDERS`),
  with a merge-blocking `assert_other_retained` counterpart to `assert_unknown_retained` and a new
  `assert_no_score_reduced` covering every artist of every identity; both are enforced by
  `wad eval`. The remainder of the promise cannot hold — a boosted artist that rises has to pass
  someone — so the note now states plainly that a sourced man's *position* can move down and that
  this is the whole of the lens's re-allocation. The score tables in both the dashboard and the
  committed static render gained a **Position** column so a reader can see why rank is not a pure
  function of total score. `tests/test_lens.py::test_lens_other_is_not_penalised_like_unknown`,
  which had asserted only that two boosts were both `0.0` and stayed green throughout the defect,
  now asserts the rank protection its name describes.
- A band whose only sourced front-person is a nonbinary artist is no longer described as a
  "female-fronted band" (#69). `BandComposition.female_fronted` now means only "a front-person's
  own sourced gender is `WOMAN`" and no longer reads the lens's `VALUES_ALIGNED_GENDERS`, so lens
  policy can never change what the data model asserts; the unflattened fact is the new
  `BandComposition.sourced_front_genders`, which every rendered label, the identity-coverage
  readout, and the fairness segmentation (new `nonbinary-fronted` segment) are written from. The
  lens keeps boosting such bands by intersecting its own aligned set with the sourced front
  genders, so no artist loses exposure to the fix. `tests/test_front_person_labels.py` asserts
  the absence of the harm across every surface `recommender/why.py` feeds.
- `mean_recall_delta` in the multiworld eval report was one world's number divided by five.
  Four of the five fixture worlds have a four-candidate rankable pool at the default `k=5`, so
  the top-k is the entire pool for every possible ordering and `recall_at_k` is 1.0 for any
  ranker — a perfect one, a random one, a reversed one — including the popularity baseline.
  Those four contributed a structurally-pinned `recall_delta` of 0.0, turning
  demo-tuned-indie's measured 0.5 into a published headline of 0.1. Recall is now reported
  per world with `recall_discriminates` and a note naming the pool size, `mean_recall_delta`
  averages only the worlds where recall could vary, and
  `n_worlds_recall_discriminating` sits next to it so the denominator is never implicit.
  The report's own caveat told readers to trust the aggregate over any single world; the
  aggregate's recall signal came from that single world.
- The eval verdict accepted a draw. `hybrid_beats_popularity` was true when MAP@k tied and
  recall was merely not worse — a condition an exact tie on every metric in every world
  satisfies, and one the recall half of which was pinned true anyway in the four worlds above.
  Both the per-world and aggregate rules now require a strict MAP@k improvement, which is what
  README's merge-blocking "the offline eval must beat the popularity baseline" says.
- OpenSSF Scorecard workflow comments now describe the repository's current
  public publishing path instead of its superseded restricted-publication
  posture.
- 320 px reflow defect caught by the new browser specs: the score-summary and fairness tables
  forced page-level horizontal scrolling at narrow widths (WCAG 2.2 §1.4.10). Data tables now sit
  in keyboard-focusable, labelled scroll regions (`role="region"`, `tabindex="0"`,
  `overflow-x: auto`) so only the excepted two-dimensional content scrolls — Arrow keys operate
  it, and the page itself reflows; long citation URLs additionally wrap (`overflow-wrap`).

### Security
- Declared `pillow>=12.3` explicitly in the `app` extra (PYSEC-2026-2253 through
  PYSEC-2026-2257), so the constraint no longer relies on Streamlit's transitive
  floor; `uv.lock` already resolved Pillow 12.3.0.
- `persist-credentials: false` on the CI checkout step, so the default `GITHUB_TOKEN` is not
  persisted for later steps (#4).
- All GitHub Actions `uses:` pinned to 40-character commit SHAs with version comments, closing the
  prior floating-tag supply-chain gap (#4, kept current by Renovate's digest pinning).
- Dependency security refresh: documented and waived the 19-advisory Python-3.9-EOL cluster
  (`requests`, `urllib3`, `streamlit`, `pillow`, `pyarrow`, `msgpack`, `filelock`, `pytest`, `pip`)
  with a committed, justified VEX (#5), then **resolved it outright** via the Python 3.10+
  migration (#6) — `pip-audit` now runs with an **empty** waiver list and no `--ignore-vuln` flags.
  See `docs/audits/residual-risk.md` (RR-1, RR-4) and `docs/audits/vex.json`.

---

*Older history predating 2026-06-29 is not available — the git history backing this repository was
reset on that date (see `docs/ROADMAP.md` and the audit trail in `audit-2026-07-05/` for context);
this changelog starts from the current history's initial commit forward.*
