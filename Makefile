# Lavender Rotation — single source of truth for the local + CI gates.
# `make verify` runs the same checkable gates CI enforces (QUALITY-AND-METRICS
# STANDARD §"enforcement pipeline"), in order.

PYTHON ?= .venv/bin/python
UV     ?= uv
A11Y_HTML := docs/audits/dashboard.html
# Scheme-pinned renders (gate inputs only, not committed artifacts): auditing a
# light-pinned AND a dark-pinned render makes the a11y gate scheme-complete on
# any machine — a Dark-Mode Mac and light-mode CI check the same two palettes.
A11Y_HTML_LIGHT := /tmp/lavender-dashboard-light.html
A11Y_HTML_DARK  := /tmp/lavender-dashboard-dark.html

.DEFAULT_GOAL := help
.PHONY: help install dev verify format lint typecheck test security render a11y a11y-e2e eval eval-check eval-real i18n bench mutation stamp refresh schedule audit clean forget

# eval-real inputs (FIX-06's human-gated real-data leg — LOCAL ONLY, never CI).
EVAL_REAL_USER ?=
EVAL_REAL_DB ?=

# Periodic re-enrichment (ADR 0013) — LOCAL ONLY, never CI. The cache being
# refreshed is your listening history on your machine; a hosted runner has no
# such cache, and giving it one would mean uploading a personal listening
# profile to CI. `make schedule` prints the launchd/cron entry that runs
# `make refresh` on the recorded cadence.
LAVENDER_USER ?=
SCHEDULER ?=
# Extra flags for a scheduled or hand-run refresh, e.g. REFRESH_ARGS="--limit 50".
# Deliberately empty: the bounds live in the CLI's own defaults, so restating
# them here would be a second place for them to drift.
REFRESH_ARGS ?=

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# Bootstraps from uv.lock (CQ-09/SEC-13): `--frozen` refuses to update the lock,
# so this is also the local lockfile-drift check — if pyproject.toml and uv.lock
# have drifted apart, this fails loudly instead of silently re-resolving.
$(PYTHON): pyproject.toml uv.lock ## Bootstrap the virtualenv + dev/e2e/app deps from uv.lock (uv sync --frozen)
	$(UV) sync --frozen --group dev --group e2e --extra app
	touch $(PYTHON)

install: $(PYTHON) ## Install the project (editable) with dev + app extras, pinned via uv.lock

dev: install ## Run the Streamlit dashboard (demo mode; no API key needed)
	$(PYTHON) -m streamlit run app/dashboard.py

# --- The verify pipeline (each stage is merge-blocking) ----------------------
verify: lint typecheck test security a11y eval-check i18n ## Run every checkable gate (CI parity)
	@echo "✓ all checkable gates green"

format: ## Auto-format the code
	$(PYTHON) -m ruff format .

lint: ## Stage 1 — format check + lint (ruff, incl. bandit SAST subset)
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .
	@# CQ-34: locks in the currently-clean state — fails on any bare TODO/FIXME/HACK
	@# marker or unqualified `# noqa` (a qualified `# noqa: CODE` is fine and is
	@# ruff's own job to police via RUF100 "unused noqa").
	@if grep -rInE "TODO|FIXME|HACK" pipeline recommender app export tests scripts 2>/dev/null; then \
		echo "lint: bare TODO/FIXME/HACK marker found (CQ-34) — resolve or file a tracked issue" >&2; exit 1; \
	fi
	@if grep -rInE "#\s*noqa\s*($$|[^:])" pipeline recommender app export tests 2>/dev/null; then \
		echo "lint: blanket '# noqa' with no rule code found (CQ-35) — qualify it" >&2; exit 1; \
	fi
	@# DOC-08: CITATION.cff must stay schema-valid. Version-pinned (not bare
	@# `uvx cffconvert`) so this merge-blocking check doesn't drift silently.
	uvx cffconvert==2.0.0 --validate
	@# DOC-15: every governance/audit doc carries a currency stamp.
	@./scripts/check-staleness.sh

typecheck: ## Stage 2 — strict static typing (mypy --strict)
	$(PYTHON) -m mypy

test: ## Stage 3 — unit + integration tests with coverage gates (>=85%; identity resolver >=95%)
	$(PYTHON) -m pytest
	# Per-module floor (CODE-QUALITY-STANDARD, safety-critical paths): the identity
	# resolver must hold >=95% branch coverage, above the 85% baseline. Scoped
	# re-report over the .coverage data the pytest run just wrote.
	$(PYTHON) -m coverage report --include="pipeline/identity.py" --fail-under=95
	# Docs-currency gate: every figure the docs state about this repo must match
	# what the repo derives right now, not a hand-typed number from whenever it
	# was last edited. One manifest (scripts/docs_figures.py), one row per stated
	# claim — this is the "M8 auto-stamp backlog item" PR #49 flagged as the
	# systemic fix, replacing the single-claim scripts/check-readme-claims.py.
	# `make stamp` writes the derived values in.
	$(PYTHON) scripts/docs_figures.py

# Dependency-audit waivers (SECURITY-AND-SUPPLY-CHAIN-STANDARD §4 "Unfixable
# HIGH/CRITICAL waiver — committed, justified waiver JSON").
# As of the Python 3.10+ migration (2026-06-30) the waiver list is EMPTY:
#   * RR-4 — the 19-advisory Python-3.9-EOL cluster (requests, urllib3, streamlit,
#     pillow, pyarrow, msgpack, filelock, pytest, pip) had fixes gated to
#     Python >=3.10; every fix installs on this project's floor (see pyproject
#     floors + uv.lock), so all 19 IDs are dropped.
#   * RR-1 — GHSA-4xh5-x5gv-qwph (pip fallback tar extraction) is cleared by
#     pip>=25.3 / PEP 706 tar filter; no longer reported.
# (The floor was >=3.10 when this note was written and is >=3.12 today, per ADR
# 0004. Both statements above hold a fortiori on the higher floor; the "now
# >=3.10" wording did not, and is corrected.)
# `pip-audit` is therefore driven to 0 with NO --ignore-vuln flags. Re-introduce a
# justified entry here (byte-identical in ci.yml + docs/audits/vex.json) only if a
# genuinely-unfixable advisory ever appears. History: docs/audits/residual-risk.md.
AUDIT_IGNORES :=

security: ## Stage 4 — dependency vulnerability + secret scan
	# Audit installed deps; the waiver list is empty (see docs/audits/residual-risk.md).
	# --skip-editable: pip-audit errors on the editable-installed project itself
	# (no PyPI dist to resolve for a local `pip install -e .`); we only care about
	# third-party deps here, so skip auditing the local editable install.
	$(PYTHON) -m pip_audit --skip-editable $(AUDIT_IGNORES)
	@./scripts/secret-scan.sh

# Regenerating the *committed* artifact is deliberately NOT part of `a11y`
# (BUG #71). It used to be: `make a11y` overwrote docs/audits/dashboard.html and
# then audited the fresh copy, so the gate could never observe that the
# committed page had gone stale — it destroyed the evidence before looking. The
# committed page is browsable on GitHub and the README's Standards table links
# to it, so a silent drift is a claim about a build that no longer exists.
# `tests/test_committed_render.py` (stage 3, before this stage) asserts the
# committed bytes equal what the renderer produces today, and this target is how
# you regenerate them on purpose.
render: ## Regenerate the committed static dashboard render (docs/audits/dashboard.html)
	$(PYTHON) -m app.build_static

# The committed demo census (docs/audits/census-demo.json). Same split as
# `render`/`eval-check`: `make test` compares the committed file against a
# regeneration and never writes it, and this target is how you regenerate it on
# purpose. `--as-of` is read back out of the committed file so a regeneration is
# not silently re-dated by the calendar — a date is the only field in it that
# today could change, and a gate that fails every midnight teaches people to
# ignore it.
census: ## Regenerate the committed demo census (docs/audits/census-demo.json)
	$(PYTHON) -m pipeline.cli census \
		--as-of "$$($(PYTHON) -c 'import json,pathlib; print(json.loads(pathlib.Path("docs/audits/census-demo.json").read_text())["as_of"])')" \
		--out docs/audits/census-demo.json

a11y: ## Stage 5 — audit the COMMITTED render plus pinned light/dark renders (0 violations in BOTH schemes)
	@test -f $(A11Y_HTML) || { \
		echo "a11y: $(A11Y_HTML) is missing — run 'make render' and commit it" >&2; exit 1; }
	$(PYTHON) -m app.build_static --scheme light --out $(A11Y_HTML_LIGHT)
	$(PYTHON) -m app.build_static --scheme dark --out $(A11Y_HTML_DARK)
	@if command -v pa11y >/dev/null 2>&1; then \
		echo "running pa11y (axe runtime) over auto + light-pinned + dark-pinned renders"; \
		printf '%s\n' '{"chromeLaunchConfig":{"args":["--no-sandbox"]}}' > /tmp/pa11y-ci.json; \
		for f in $(A11Y_HTML) $(A11Y_HTML_LIGHT) $(A11Y_HTML_DARK); do \
			echo "pa11y: $$f"; \
			pa11y --runner axe --config /tmp/pa11y-ci.json $$f || exit 1; \
		done; \
	else \
		echo "pa11y not installed — using built-in static a11y checker"; \
		for f in $(A11Y_HTML) $(A11Y_HTML_LIGHT) $(A11Y_HTML_DARK); do \
			$(PYTHON) -m app.a11y_check $$f || exit 1; \
		done; \
	fi

# The specs also run inside `make test` (they auto-skip when no Chrome/Chromium
# is reachable); this dedicated entry point makes a missing browser a hard
# failure, which is exactly how CI runs them (LAVENDER_E2E_REQUIRE=1 on `make
# verify`), so local and server strictness cannot silently diverge (A11Y-03).
a11y-e2e: ## Stage 5b — browser-driven keyboard/reflow/reduced-motion specs (Playwright + Chrome)
	LAVENDER_E2E_REQUIRE=1 $(PYTHON) -m pytest tests/test_e2e_a11y.py -m e2e --no-cov -q

eval: ## Stage 7 — multi-world offline eval; fails unless hybrid beats baseline on aggregate (FIX-06)
	$(PYTHON) -m pipeline.cli eval --k 5 --out docs/audits/eval-report.json
	@# EXP-10: every quantitative claim in docs/writeup/methods.md must match the
	@# report the line above just regenerated. This used to run only in `make
	@# audit`, which is not the merge gate, so the writeup could drift on `main`
	@# for as long as nobody ran `audit` — the same "hand-typed and stale"
	@# failure `scripts/docs_figures.py` exists to prevent for the README.
	$(PYTHON) scripts/writeup-check.py

# Stage 7 as `verify` runs it. `eval` above writes the report straight into the
# working tree, which is why it could not be the gate: it regenerates the right
# numbers, throws the comparison away, and replaces the committed bytes.
#
# Measured on 2026-08-29 against this branch's parent. One metric in
# docs/audits/eval-report.json was edited to 0.0001. `git status` showed the
# file modified. `make eval` exited 0. `git status` came back empty with the
# file restored, and nothing was said. A stale committed report therefore could
# not fail anywhere, CI included, because CI runs `make verify` on a clean
# checkout and this same writing target overwrote the evidence before anything
# looked at it.
#
# scripts/writeup-check.py runs here rather than only in `audit`. It reads
# docs/audits/eval-report.json at a fixed path, so running it after `eval` only
# ever compared methods.md against a file written seconds earlier, which says
# nothing about the artifact the repository publishes. Running it after the diff
# compares methods.md against the committed report, which the diff has just
# proved is what the pipeline produces. `audit` still runs it too.
#
# `diff` exits 0 identical, 1 different, above 1 when it could not look. The
# three are kept apart, because a gate that reports success for having failed to
# run is the failure this target exists to fix.
eval-check: ## Stage 7 — offline eval, compared against the committed report
	@set -u; \
	tmp=$$(mktemp -d) || { echo "eval-check: could not make a temp dir" >&2; exit 1; }; \
	trap 'rm -rf "$$tmp"' EXIT; \
	$(PYTHON) -m pipeline.cli eval --k 5 \
	  --out "$$tmp/eval-report.json" \
	  --baseline docs/audits/eval-baseline.json || exit 1; \
	test -s "$$tmp/eval-report.json" || { \
	  echo "eval-check: the eval wrote no report" >&2; exit 1; }; \
	test -f docs/audits/eval-report.json || { \
	  echo "eval-check: docs/audits/eval-report.json is missing; run 'make eval'" >&2; exit 1; }; \
	diff -u docs/audits/eval-report.json "$$tmp/eval-report.json"; \
	d=$$?; \
	if [ $$d -eq 1 ]; then \
	  echo "eval-check: docs/audits/eval-report.json is not what the pipeline produces now." >&2; \
	  echo "Run 'make eval' and commit the regenerated report." >&2; \
	  exit 1; \
	elif [ $$d -gt 1 ]; then \
	  echo "eval-check: diff could not compare the reports (exit $$d)." >&2; \
	  echo "Refusing to report success for a check that did not happen." >&2; \
	  exit $$d; \
	fi; \
	echo "eval-check: the committed eval report is what the pipeline produces."
	@$(PYTHON) scripts/writeup-check.py

# NOT part of verify/audit, and must NEVER run in CI (FIX-06's human-gated
# real-data leg — see recommender/eval.py::eval_real). Run locally only, on
# your own cache DB, e.g.:
#   make eval-real EVAL_REAL_USER=yourname EVAL_REAL_DB=data/cache.db
eval-real: ## LOCAL-ONLY — real-data eval leg against your own cached scrobbles; never CI
	@test -n "$(EVAL_REAL_USER)" || { echo "usage: make eval-real EVAL_REAL_USER=<lastfm-username> EVAL_REAL_DB=<path-to-cache.db>"; exit 1; }
	@test -n "$(EVAL_REAL_DB)" || { echo "usage: make eval-real EVAL_REAL_USER=<lastfm-username> EVAL_REAL_DB=<path-to-cache.db>"; exit 1; }
	$(PYTHON) -m pipeline.cli eval-real --user "$(EVAL_REAL_USER)" --scrobbles "$(EVAL_REAL_DB)"

# Periodic re-enrichment, the operator-facing half (ADR 0013). LOCAL ONLY, never
# CI: this reads your own cache and reaches MusicBrainz/Wikidata. One run is
# bounded by the CLI's own `--limit` default and rotates stalest-first, so a
# whole catalog is several runs and each resumes where the last stopped. It
# exits non-zero when the upstream answered nothing — a silent source is
# reported as unreachable, never as agreement, so a red scheduled run is real.
refresh: ## LOCAL-ONLY — re-ask upstream about the stalest cached artists (needs LAVENDER_USER)
	@test -n "$(LAVENDER_USER)" || { echo "usage: make refresh LAVENDER_USER=<lastfm-username> [REFRESH_ARGS=\"--limit 50\"]"; exit 1; }
	$(PYTHON) -m pipeline.cli refresh --user "$(LAVENDER_USER)" $(REFRESH_ARGS)

# The schedule itself. Prints; installs nothing. A job that runs on your behalf
# should be something you read and paste, not something a `make` target wrote
# into your login session. Credentials are never rendered into the entry — it
# sources a mode-600 env file the script's output tells you how to create.
schedule: ## Print the launchd/cron entry that runs `make refresh` on the ADR 0013 cadence
	@test -n "$(LAVENDER_USER)" || { echo "usage: make schedule LAVENDER_USER=<lastfm-username> [SCHEDULER=launchd|cron]"; exit 1; }
	@$(PYTHON) scripts/refresh_schedule.py --user "$(LAVENDER_USER)" $(if $(SCHEDULER),--scheduler $(SCHEDULER))

i18n: ## Stage 8 — i18n N/A declaration gate (INTERNATIONALIZATION-STANDARD §1)
	@./scripts/i18n-gate.sh

bench: ## Benchmark the scoring path on a generated 5k-artist / 50k-scrobble world
	$(PYTHON) scripts/bench.py

# Deliberately NOT part of `verify`: a full-suite-per-mutant run takes minutes,
# not seconds. It runs weekly + on demand in CI (.github/workflows/mutation.yml)
# and any time locally. Requires a clean checkout of the two target files —
# cosmic-ray mutates them in place and restores them (guarded in the script).
mutation: $(PYTHON) ## Mutation-test identity.py + rerank.py (CQ-47; fails under 70% mutants killed; slow)
	@./scripts/mutation-gate.sh

# The write half of the stage-3 docs-figures gate. Deliberately a separate
# target: `make test` must never rewrite the documents it is checking, or the
# gate would pass by editing the evidence (the same failure `eval` had before
# `eval-check` split it in two). Run it after a `make test` that reported drift —
# the coverage figure is read from the `.coverage` that run just wrote, so a
# stamp on a cold checkout says so rather than inventing a number.
stamp: ## Write the derived value into every docs figure that has drifted (see `make test`)
	$(PYTHON) scripts/docs_figures.py --write

audit: render census a11y eval ## Regenerate all committed responsible-tech artifacts
	$(PYTHON) -m pytest -q >/dev/null
	@$(PYTHON) scripts/writeup-check.py
	@echo "✓ audit artifacts regenerated under docs/audits/"

clean: ## Remove build/tool caches (NOT your listening data — see `make forget`)
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
	@# `data/*.db` is a legacy location: the cache moved to the platform
	@# user-data directory (pipeline/paths.py) and this line stopped matching it.
	@# It is kept so an old checkout is still tidied, and it is no longer
	@# advertised as the deletion path — `make clean` deleting a person's real
	@# listening history would also be the wrong behaviour for a target whose
	@# job is build artifacts.
	rm -f data/*.db
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# The deletion path privacy-notes.md points at. Separate from `clean` on
# purpose: this removes personal data, so it must be asked for by name.
forget: ## Delete the local cache (listening history + resolved identity). Irreversible.
	@$(PYTHON) -c "from pipeline.paths import default_db_path; print(default_db_path())"
	@printf 'Delete the cache above? [y/N] ' && read ans && [ "$$ans" = "y" ] || { echo "aborted"; exit 1; }
	@$(PYTHON) -c "from pathlib import Path; from pipeline.paths import default_db_path; p = Path(default_db_path()); p.unlink() if p.exists() else None; print('removed', p) if not p.exists() else None"
