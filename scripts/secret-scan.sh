#!/usr/bin/env bash
# Lightweight secret scan (stand-in for gitleaks when it is not installed).
# CI uses gitleaks; this gives the same merge-blocking guarantee locally.
#
# The fallback below was rewritten after the same script, in a sibling
# repository, was found to report "0 findings" on trees that held a secret. The
# defects it had, all of which this version fixes:
#
#   1. The file list came from `git ls-files ... || true`, so running outside a
#      work tree produced an empty list and a cheerful exit 0.
#   2. The list was restricted to six extensions, so a key committed in any
#      other file type was never read. `grep -I` skips binaries, so there is no
#      reason to filter by extension at all.
#   3. `if echo "$files" | xargs grep ...` decided on xargs' exit status. Once
#      the list exceeds ARG_MAX, xargs splits it and runs grep per batch; a run
#      where one batch matches and another does not is read as "no match", and
#      it is not only the last batch's status that is lost.
#   4. The private-key pattern begins with `-`, so `grep -InE "$pat"` parsed it
#      as an option bundle, exited 2, and `2>/dev/null` hid the error while the
#      `if` read the status as "no match". Every grep here passes its pattern
#      after `-e`. (This comment describes the header rather than quoting it:
#      the scan reads this file too.)
#   5. Discarding grep's stderr meant a file the scan could not read counted as
#      a file with no secret in it. stderr is captured now, and anything on it
#      fails the run: a tree the scan could not read in full is a tree it
#      cannot vouch for.
#
# What this guarantees: it reads every tracked file, refuses to run on a list it
# could not build or that came back empty, decides on captured output rather
# than a batched exit status, fails if any part of the tree could not be read,
# and checks each pattern against a known-positive sample first, so a pattern
# edited into something inert fails loudly instead of quietly finding nothing.
set -euo pipefail

if command -v gitleaks >/dev/null 2>&1; then
  exec gitleaks detect --no-banner --redact
fi

patterns=(
  'AKIA[0-9A-Z]{16}'                       # AWS access key id
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'     # private keys
  'xox[baprs]-[0-9A-Za-z-]{10,}'           # slack tokens
  'AIza[0-9A-Za-z_\-]{35}'                 # google api key
  '(secret|password|api[_-]?key)[[:space:]]*=[[:space:]]*["'"'"'][^"'"'"']{12,}'
)

# One known-positive per pattern, in the same order, assembled from fragments at
# run time so that no line of this file is itself a match.
samples=(
  "AKIA$(printf 'A%.0s' {1..16})"
  "-----BEGIN RSA PRIVATE $(printf 'KEY')-----"
  "xox""b-0123456789abcdef"
  "AIza$(printf 'b%.0s' {1..35})"
  "password""=\"correcthorsebatterystaple\""
)

if [ "${#patterns[@]}" -ne "${#samples[@]}" ]; then
  echo "secret-scan: ${#patterns[@]} patterns but ${#samples[@]} samples" >&2
  exit 1
fi

for i in "${!patterns[@]}"; do
  if ! printf '%s\n' "${samples[$i]}" | grep -qIE -e "${patterns[$i]}"; then
    echo "secret-scan: pattern $((i + 1)) no longer matches its own sample;" >&2
    echo "             the scan would report 0 findings whatever the tree holds" >&2
    exit 1
  fi
done

list=$(mktemp)
errs=$(mktemp)
trap 'rm -f "$list" "$errs"' EXIT

if ! git ls-files -z >"$list"; then
  echo "secret-scan: could not list tracked files (not a git work tree?);" >&2
  echo "             refusing to report a clean scan of a tree it never read" >&2
  exit 1
fi

count=$(tr -cd '\0' <"$list" | wc -c | tr -d ' ')
if [ "$count" -eq 0 ]; then
  echo "secret-scan: no tracked files to scan — refusing to report success" >&2
  exit 1
fi

found=0
for pat in "${patterns[@]}"; do
  # Captured, not piped into `if`: see defects 3 and 5 in the header.
  : >"$errs"
  hits=$(xargs -0 grep -InE -e "$pat" <"$list" 2>"$errs" || true)
  if [ -s "$errs" ]; then
    echo "secret-scan: part of the tree could not be read, so a clean result" >&2
    echo "             would mean nothing. A tracked file deleted in the" >&2
    echo "             working tree, or one the current user cannot read," >&2
    echo "             will do this. grep reported:" >&2
    cat "$errs" >&2
    exit 1
  fi
  # Honor gitleaks' inline allowlist marker so the fallback and the real
  # gitleaks scan agree on deliberate test canaries (e.g. tests/test_doctor.py).
  hits=$(printf '%s' "$hits" | grep -v -e 'gitleaks:allow' || true)
  if [ -n "$hits" ]; then
    printf '%s\n' "$hits"
    found=1
  fi
done

if [ "$found" -ne 0 ]; then
  echo "secret-scan: potential secret detected (above)" >&2
  exit 1
fi
echo "secret-scan: 0 findings across $count tracked files"
