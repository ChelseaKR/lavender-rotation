# Improvement plan — audit of 2026-08-28

Last verified: 2026-08-28

Working notes for an external audit pass. Nothing in this pass is committed:
every change is left in the working tree by instruction. This file is the
durable record of the plan and its running log.

## 0. State found

- `make verify` is **green** locally, exit 0: 756 tests, 95.82% line coverage,
  pa11y/axe over three renders, Playwright e2e specs, pip-audit clean,
  gitleaks clean, eval beats baseline, i18n gate ok.
- `lavender recommend`, `lavender recommend --lens-name queer`, and
  `lavender doctor` all run offline and produce the documented output.
- 2 of the last 20 CI runs red. Both diagnosed below; both were the same real
  defect, already remediated on `main` by #94.

## 1. CI diagnosis

| run | workflow | date | verdict |
|---|---|---|---|
| 32812362407 | `ci` (scheduled, main) | 2026-08-25 | **real defect**, since fixed |
| 32690734824 | `osv-scanner` (scheduled, main) | 2026-08-24 | **real defect**, since fixed |

Both are the same finding from two scanners: `pip 26.1.2` / `PYSEC-2026-3721`
(fix in 26.2). `ci` failed at `make security` -> `pip_audit` (`Makefile:95`);
`osv-scanner` failed on the same advisory read out of `uv.lock` with
`--fail-on-vuln=true`. Neither is a flake: both are deterministic, and both
scanners agreed. PR #94 (`bc61085`) moved the lock to `pip 26.2.1`; `uv.lock`
now pins 26.2.1 and local `pip-audit` reports "No known vulnerabilities found".
No action needed beyond recording it.

## 2. Ranked plan

Ranking is by the portfolio's governing rule first (a check that cannot fail is
worse than no check), then by open-issue severity, then by unfiled defects.

### Phase A — guards that are structurally incapable of firing

- **A1. The export leak guard is blind to the queer axis.**
  `tests/test_export_schema.py` is named in ADR 0011 as one of the defenses that
  became *load-bearing* when the repo started holding orientation and trans
  data. Its `FORBIDDEN_FIELDS` / `FORBIDDEN_CONTENT_TOKENS` lists were written
  before ADR 0011 and contain no queer-axis vocabulary at all: no `queer`,
  `orientation`, `p91`, `lesbian`, `trans`, `asexual`. An export that leaked the
  single most sensitive field in the repo would pass this gate green.
- **A2. The no-inference centrepiece is blind to the queer axis.**
  `tests/test_no_inference.py::IDENTITY_CONSTRUCTORS` lists the gender and
  composition constructors and omits `QueerIdentity` / `resolve_queer_identity`.
  Two consequences: `recommender/`, `app/` and `export/` are *not* actually
  asserted to construct no identity object (only no *gender* identity object),
  and the stricter taint check that exempt tag-handling functions are held to
  cannot see a tag reaching the queer resolver.
  `test_the_permitted_guard_runs_on_the_resolver_path` also covers only two of
  the three entry points that now call `assert_permitted_only`.
- **A3. `docs/I18N.md` is exempt from the currency gate on a false premise.**
  `scripts/check-staleness.sh` skips it saying "`scripts/i18n-gate.sh` checks
  its `Declared: YYYY-MM-DD - Reviewer: ...` line". `i18n-gate.sh` checks no such
  thing. The one doc excused from the docs-currency gate is the one doc no gate
  checks the currency of.
- **A4. `app/` is not measured by the coverage gate at all.**
  `addopts` has `--cov=pipeline --cov=recommender --cov=export` and no
  `--cov=app`, so `[tool.coverage.run] omit = ["app/dashboard.py", ...]` is dead
  configuration and #71's narrowing of that omit achieved nothing. The fallback
  a11y checker (`app/a11y_check.py`) — the gate that runs whenever pa11y is not
  installed — sits at 82% with its violation detectors partly unexercised.

### Phase B — the open issues

- **B1. #93** `refresh` drops a newly-sourced queer citation and can never
  reconcile a queer-axis correction. Root cause `pipeline/ingest.py::_identity_sources`.
- **B2. #92** the queer lens's own evidence never reaches any surface, and
  `upstream_edit_url` has no `wikidata-p91` case.
- **B3. #82** the reporting half is already fixed in `recommender/eval.py`
  (`recall_discriminates`, subset mean, strict `aggregate_beats`). The fixture
  half is not: four of five worlds still have a rankable pool of 4 against
  `k=5`, so recall there is a tautology.
- **B4. #54** externally gated (Apple Music needs a paid membership, Qobuz needs
  partner approval). Tidal already ships. Record status; do not fake it.

### Phase C — unfiled defects

Populated from the two sweeps (doc/behavior drift; input validation and error
paths). See the log.

### Phase D — verification

`make verify < /dev/null; echo "EXIT=$?"` clean, plus a deliberate break/restore
for every guard added or repaired, both directions recorded.

### Phase C — unfiled defects (from two sweeps)

Input validation and error paths, ranked; and documentation-vs-behavior drift.
Everything actually fixed is listed in the log below; everything identified and
deliberately not fixed is listed under "Identified, not fixed" at the end.

## 3. Running log

- 2026-08-28: repo read, `make verify` green, CI failures diagnosed, plan drafted.
- A1 done. `tests/test_export_schema.py` extended to ADR 0011's axis, with a
  fixture world that actually holds a sourced queer, trans artist so the
  assertions have something to observe. Break/restore recorded: with an export
  leaking `[lesbian]` into every format, the pre-existing
  `test_no_export_format_leaks_identity_vocabulary` **passed**; the new test
  failed.
- A2 done. `IDENTITY_CONSTRUCTORS` grew `QueerIdentity` / `resolve_queer_identity`
  / `_map_orientation`; the permitted-guard test now covers the third resolver;
  a new test derives the guard's caller set from the AST and asserts its
  docstring names them (it caught the docstring saying "the two entry points"
  while three called it). Break/restore recorded: a `QueerIdentity(...)`
  construction added to `recommender/rerank.py` is caught now and was invisible
  to the pre-edit constructor set.
- A3 done. `scripts/i18n-gate.sh` now enforces the `Declared:` date and
  `Reviewer:` name that `scripts/check-staleness.sh` already claimed it did;
  that comment is corrected too. Break/restore recorded in both directions, and
  the staleness gate confirmed to exit 0 on the same broken file (which is why
  the substitute enforcement had to live in the i18n gate).
- A4 done. `--cov=app` added, which makes `[tool.coverage.run] omit` live
  configuration instead of dead. `app/a11y_check.py` went 82% -> 100%: its
  `main()`, the entry point `make a11y` runs when pa11y is absent, had never
  been executed by a test. Tests added for `app/build_static.py`'s CLI too.
  Break/restore recorded for both: a `main()` that prints violations and returns
  0, and a `--scheme` the CLI accepts and ignores.
- B1 done (#93). `pipeline/ingest._identity_sources` now includes
  `artist.queer.sources`, deduplicated; `_diff_sources` matches on
  (kind, citation) with a kind-only fallback so two `artist-statement` citations
  answering different questions are not each other's "old value". Three
  regression tests; all three fail on the reverted code.
- B2 done (#92). `Explanation.queer_sources` and `WhyThisArtist.queer_provenance`
  carry ADR 0011's axis to the CLI, the static render and the dashboard under
  their own heading; `upstream_edit_url` gained `wikidata-p91` (anchored `#P91`)
  via a derived map that a test holds to the enum. Break/restore recorded.
- B3 done (#82, remaining half). The four non-demo fixture worlds grew past
  `k=5`; all five worlds now discriminate on recall and
  `recall_pinned_worlds` is empty. Guard tests assert the pools stay bigger
  than `k`, that the pinned-world machinery still fires when `k` is too big, and
  that a draw does not pass the beat-the-baseline gate. Break/restore recorded.
- `scripts/writeup-check.py` hardened: it silently skipped any annotation whose
  value sat inside a code span, so `hybrid_beats_popularity` and `n_positives`
  wore citations nothing verified. It now reads both forms and *fails* on an
  annotation it cannot read. Wired into `make eval`, so it is merge-blocking
  rather than `make audit`-only. Break/restore recorded for both directions.
- C: `pipeline/paths.py` logged to `wad.paths`, outside the configured
  `lavender` tree, so its records bypassed the project's only handler and the
  stderr-only privacy invariant. Fixed, `get_logger` now prefixes rather than
  passing a bare name through, and `tests/test_log_privacy.py` gained a third
  leg that derives every logger name from the AST plus a test demonstrating that
  a stray namespace really does escape the handler.
- C: `pipeline/lastfm.py` container reads hardened (`_as_list`), Last.fm's
  HTTP-200 error envelope is detected, never cached, and never replayed as data,
  and pagination is bounded by `MAX_PAGES`.
- C: `refresh_catalog`'s cache read moved inside the per-artist failure
  boundary, honoring its own docstring; a negative `PRAGMA user_version` now
  raises `CacheSchemaError` instead of `KeyError`; `average_precision_at_k`
  refuses a non-positive `k`; `--lens` and `--explore` are validated to [0, 1];
  `lavender corrections` refuses an unmappable value or a non-ISO date instead
  of reporting success for a row that could never take effect.
- D: `pipeline.cli.build_parser()` extracted, and
  `tests/test_documented_commands.py` added: every `lavender ...` invocation in
  the repo's Markdown must name a real subcommand and real flags. It fires on the
  corrections-add invocation that README.md and CONTRIBUTING.md both documented
  and that has never existed (spelled without backticks here on purpose: this
  gate reads code spans, and quoting a broken command in one would trip it). Break/restore recorded.
- D: documentation corrected against behavior. Seven governance/audit documents
  (model card, data card, residual-risk, AI risk register, identity-data-ethics,
  research roadmap) asserted that no live enrichment client exists and that
  `lavender refresh` is fixture-only. That was true when written and false from
  FIX-01 onward. These are the repo's compliance evidence, so being wrong about
  a shipped capability in them is worse than the CLI typo. Also corrected: the
  documented deletion path for personal data (`make clean` matched a `data/`
  directory the cache left years ago, so the documented way to delete your
  listening history deleted nothing; there is now a `make forget` that resolves
  the real path through `pipeline.paths`); the Python floor stated as `>=3.10`
  in four places after ADR 0004 moved it to `>=3.12`; ADR 0004's own unexecuted
  decision to drop the Streamlit mypy override (removed, and strict mypy passes
  with Streamlit followed); `lavender doctor`'s upstream probe listing four APIs
  and pinging Discogs, which nothing in this codebase has ever fetched from;
  SECURITY.md describing a single-egress surface that grew to four; the
  workflow inventories omitting `mutation.yml`, which the README cites as an
  active gate; hand-typed doc/test/workflow counts replaced with the command to
  recompute them; and the remaining pre-rename `wad` strings in code, the
  Makefile, the mutation script and the docs.
- D: `tests/test_readme_claims.py` gained a Python-floor consistency gate: the
  floor is read from `pyproject.toml` and the prose, the ruff target, the mypy
  target and the CI matrix are all held to it. Break/restore recorded in both
  directions.

## 4. Verification

`make verify < /dev/null; echo "EXIT=$?"` -> **EXIT=0**, 901 tests, 96.26%
coverage. `make audit` -> exit 0, no artifact drift. `make a11y-e2e`
(LAVENDER_E2E_REQUIRE=1, the strict form CI uses) -> exit 0, 6 browser specs.

Every guard added or repaired was broken deliberately, watched fail, restored,
and watched pass. The results are listed against each entry in the log above.

`make mutation` (CQ-47) cannot run here: the gate refuses a tree with uncommitted
changes in `pipeline/identity.py` or `recommender/rerank.py`, and this pass is
required to leave everything uncommitted. That refusal is the guard working, not
a defect. It was run instead against an isolated copy of the tree, on the one
target this pass changed:

```
pipeline/identity.py  207 mutants, 26 surviving = 12.56% survival
cr-rate --fail-over 30  ->  exit 0   (ceiling is 30%)
```

`recommender/rerank.py` is byte-identical to `main` and was not re-run.

## 5. Identified, not fixed

Recorded rather than silently dropped. None is a regression introduced here.

**Error paths and input validation**

- A corrupt cache row still aborts `profile_from_cache` / `catalog_from_cache`.
  Fixed only in `refresh_catalog`, whose docstring already promised per-artist
  isolation. Failing loudly on a corrupt cache is defensible; silently skipping
  rows would hide data loss, and choosing between those is a design decision
  with its own ADR, not a drive-by fix.
- `Cache()` raises `sqlite3.DatabaseError` / `OperationalError` / `PermissionError`
  straight out of every CLI command; only `lavender doctor` catches them. A
  top-level handler in `main()` mapping storage failures to a clean message and
  exit code would fix it, and changes every command's error contract.
- `LAVENDER_DATA_DIR` naming an existing file breaks *import* of
  `pipeline.cache` (module-level `DEFAULT_DB_PATH = default_db_path()` calls
  `mkdir`), so even `lavender --help` fails and `doctor`'s careful `except
  OSError` never runs. The fix is lazy path resolution in a module every command
  imports.
- Malformed or half-written `pending-corrections.json` raises from
  `reconcile_after_refresh`, i.e. *after* a refresh has already rewritten the
  cache; and `_write_all` is a non-atomic whole-file rewrite with no temp file.
- `lavender eval-real --scrobbles <typo>` creates an empty SQLite file at the
  typo'd path as a side effect, then raises.
- `LastfmRequestError` is caught nowhere outside the module that raises it, so a
  bad `--user`, a revoked key or a dead network gives `lavender ingest` a
  traceback.
- `MusicBrainzEnricher.resolve_mbid` memoizes a *failed* resolution
  indistinguishably from an ambiguous one, so one throttled search poisons every
  later lookup for that artist in the same process.
- `pending-corrections add --source-kind` has no `choices`, so an unrecognized
  kind files a row that can never reconcile and offers no edit link.
- A mid-batch export failure leaves a partly-populated playlist and returns no
  `PlaylistExport` to say so, against `export/base.py`'s stated contract; there
  is no 429/`Retry-After` handling in `export/`.
- `--out` paths are unvalidated in four commands; `Scrobble.ts` has no type
  invariant; `scripts/upstream_worklist.py` has an unguarded per-row
  `json.loads`.

**Documentation**

- `docs/ideation/*` and `CHANGELOG.md` still use pre-rename names and describe
  unshipped proposals. Both are explicitly dated working notes/history and are
  exempt from the new command gate for that reason.
- The live status of `docs/audits/branch-ruleset.json` cannot be settled from
  inside the repo: `README.md` says the ruleset is configured, and
  `RESPONSIBLE-TECH-AUDITS.md` and ADR 0001 say it is proposed and not yet
  applied. One of the three is wrong and only GitHub state can say which.
- `docs/README.md` indexes twelve documents and omits `docs/adr/` and
  `docs/audits/` entirely.
- `docs/adr/0001` and `docs/ROADMAP.md` reference the same external audit file
  under two different names (`women-artist-discovery-AUDIT.md` /
  `lavender-rotation-AUDIT.md`); the file is outside this repo.

**Open issue #54** (multi-platform export) is externally gated and stays open:
Apple Music requires a paid Apple Developer Program membership for a developer
token, and Qobuz's API is partner-approval-gated. Neither can be obtained from
here. The parts that were in reach already ship: `export/base.py` defines the
`Exporter` protocol seam the issue asks for, TIDAL is a working adapter beside
Spotify, and the credential-free portable formats are the documented today-path
for every other platform.
