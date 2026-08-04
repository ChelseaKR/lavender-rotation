# Women-Artist Discovery — single source of truth for the local + CI gates.
# `make verify` runs the same checkable gates CI enforces (QUALITY-AND-METRICS
# STANDARD §"enforcement pipeline"), in order.

PYTHON ?= .venv/bin/python
UV     ?= uv
A11Y_HTML := docs/audits/dashboard.html
# Scheme-pinned renders (gate inputs only, not committed artifacts): auditing a
# light-pinned AND a dark-pinned render makes the a11y gate scheme-complete on
# any machine — a Dark-Mode Mac and light-mode CI check the same two palettes.
A11Y_HTML_LIGHT := /tmp/wad-dashboard-light.html
A11Y_HTML_DARK  := /tmp/wad-dashboard-dark.html

.DEFAULT_GOAL := help
.PHONY: help install dev verify format lint typecheck test security a11y a11y-e2e eval eval-real i18n bench mutation audit clean

# eval-real inputs (FIX-06's human-gated real-data leg — LOCAL ONLY, never CI).
EVAL_REAL_USER ?=
EVAL_REAL_DB ?=

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
verify: lint typecheck test security a11y eval i18n ## Run every checkable gate (CI parity)
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
	# Docs-currency guard: README's "NNN tests at NN% coverage" claim must match
	# this run, not a hand-typed number from whenever it was last edited (the
	# "M8 auto-stamp backlog item" PR #49 flagged as still open).
	$(PYTHON) scripts/check-readme-claims.py

# Dependency-audit waivers (SECURITY-AND-SUPPLY-CHAIN-STANDARD §4 "Unfixable
# HIGH/CRITICAL waiver — committed, justified waiver JSON").
# As of the Python 3.10+ migration (2026-06-30) the waiver list is EMPTY:
#   * RR-4 — the 19-advisory Python-3.9-EOL cluster (requests, urllib3, streamlit,
#     pillow, pyarrow, msgpack, filelock, pytest, pip) had fixes gated to
#     Python >=3.10; with the floor now >=3.10 every fix installs (see
#     pyproject floors + uv.lock), so all 19 IDs are dropped.
#   * RR-1 — GHSA-4xh5-x5gv-qwph (pip fallback tar extraction) is cleared by
#     pip>=25.3 / PEP 706 tar filter on the >=3.10 floor; no longer reported.
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

a11y: ## Stage 5 — render the dashboard (auto + pinned light/dark) and run the a11y gate (0 violations in BOTH schemes)
	$(PYTHON) -m app.build_static
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
# failure, which is exactly how CI runs them (WAD_E2E_REQUIRE=1 on `make
# verify`), so local and server strictness cannot silently diverge (A11Y-03).
a11y-e2e: ## Stage 5b — browser-driven keyboard/reflow/reduced-motion specs (Playwright + Chrome)
	WAD_E2E_REQUIRE=1 $(PYTHON) -m pytest tests/test_e2e_a11y.py -m e2e --no-cov -q

eval: ## Stage 7 — multi-world offline eval; fails unless hybrid beats baseline on aggregate (FIX-06)
	$(PYTHON) -m pipeline.cli eval --k 5 --out docs/audits/eval-report.json

# NOT part of verify/audit, and must NEVER run in CI (FIX-06's human-gated
# real-data leg — see recommender/eval.py::eval_real). Run locally only, on
# your own cache DB, e.g.:
#   make eval-real EVAL_REAL_USER=yourname EVAL_REAL_DB=data/cache.db
eval-real: ## LOCAL-ONLY — real-data eval leg against your own cached scrobbles; never CI
	@test -n "$(EVAL_REAL_USER)" || { echo "usage: make eval-real EVAL_REAL_USER=<lastfm-username> EVAL_REAL_DB=<path-to-cache.db>"; exit 1; }
	@test -n "$(EVAL_REAL_DB)" || { echo "usage: make eval-real EVAL_REAL_USER=<lastfm-username> EVAL_REAL_DB=<path-to-cache.db>"; exit 1; }
	$(PYTHON) -m pipeline.cli eval-real --user "$(EVAL_REAL_USER)" --scrobbles "$(EVAL_REAL_DB)"

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

audit: a11y eval ## Regenerate all committed responsible-tech artifacts
	$(PYTHON) -m pytest -q >/dev/null
	@$(PYTHON) scripts/writeup-check.py
	@echo "✓ audit artifacts regenerated under docs/audits/"

clean: ## Remove caches and generated local data
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov .coverage
	rm -f data/*.db
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
