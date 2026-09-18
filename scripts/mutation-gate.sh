#!/usr/bin/env bash
# Mutation-testing gate (CQ-47) over the two safety-critical modules:
#
#   * pipeline/identity.py   — sourced-never-inferred identity resolver
#   * recommender/rerank.py  — boost-only / never-penalize values lens
#
# For each module, cosmic-ray generates mutants, runs the FULL unit suite
# against every one (subprocess per mutant — identical semantics to a real
# `pytest` run), and the gate fails if fewer than 70% of mutants are killed
# (cr-rate --fail-over 30 == survival above 30% fails), per module.
#
# cosmic-ray is version-pinned inline, same convention as pa11y in ci.yml —
# not a dependency-group entry, so the lockfile stays scoped to tools the
# regular verify pipeline needs. `--no-project` keeps the tool env from
# touching the project's .venv; the test-command inside the configs uses
# .venv/bin/python explicitly (run `make install` first).
#
# NOTE: cosmic-ray's local distributor applies each mutation to the working
# copy of the target file and restores it afterwards. Run on a clean checkout;
# the guard below refuses to run if a target file has uncommitted changes so a
# crash mid-run cannot silently mix a mutant into your diff.
set -euo pipefail

CR_PIN="cosmic-ray==8.4.1"
run_cr() { uv run --no-project --with "$CR_PIN" "$@"; }

TARGETS=(pipeline/identity.py recommender/rerank.py)
if ! git diff --quiet -- "${TARGETS[@]}"; then
  echo "mutation-gate: uncommitted changes in ${TARGETS[*]} — commit or stash first" >&2
  echo "(cosmic-ray mutates these files in place and restores them; a dirty" >&2
  echo "tree risks mixing a mutant into your work if the run is interrupted)" >&2
  exit 1
fi

WORK=$(mktemp -d "${TMPDIR:-/tmp}/lavender-mutation.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

for cfg in scripts/mutation/identity.toml scripts/mutation/rerank.toml; do
  name=$(basename "$cfg" .toml)
  session="$WORK/$name.sqlite"
  echo "mutation-gate: $name — generating mutants"
  run_cr cosmic-ray init "$cfg" "$session"
  echo "mutation-gate: $name — baseline (unmutated suite must pass)"
  run_cr cosmic-ray baseline "$cfg"
  echo "mutation-gate: $name — executing (full suite per mutant; minutes, not seconds)"
  run_cr cosmic-ray exec "$cfg" "$session"
  run_cr cr-report "$session" | tail -n 3
  echo "mutation-gate: $name — enforcing >=70% killed"
  run_cr cr-rate "$session" --fail-over 30
done

echo "mutation-gate: both safety-critical modules meet the >=70% kill threshold — ok"
