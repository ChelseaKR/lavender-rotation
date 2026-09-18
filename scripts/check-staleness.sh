#!/usr/bin/env bash
# Docs currency gate (DOC-15): every governance/audit doc must carry a
# `Last verified: YYYY-MM-DD` stamp. This checks presence + well-formedness,
# not recency — judging *whether* a doc is still accurate per its own stated
# recheck cadence is a REVIEW-gated human call, not something to fake as AUTO.
set -euo pipefail

# docs/adr/* are dated, immutable decision records (a new ADR supersedes an old
# one rather than editing it) — no "Last verified" stamp applies.
# docs/ideation/* are working notes, not committed governance/audit docs.
# docs/I18N.md has its own dedicated, stricter enforcement gate
# (scripts/i18n-gate.sh checks its "Declared: YYYY-MM-DD · Reviewer: ..." line).
# That claim was false until 2026-08-28: i18n-gate.sh checked the status
# declaration and the Reason line only, so this exclusion handed the one doc it
# names a pass that nothing else issued. i18n-gate.sh now really does enforce
# the Declared date and the Reviewer name, and this exclusion is honest.
files=$(find docs -maxdepth 2 -name '*.md' \
  -not -path 'docs/adr/*' \
  -not -path 'docs/ideation/*' \
  -not -path 'docs/I18N.md' \
  2>/dev/null | sort)

missing=0
for f in $files; do
  if ! grep -qE 'Last verified: [0-9]{4}-[0-9]{2}-[0-9]{2}' "$f"; then
    echo "staleness-gate: $f has no 'Last verified: YYYY-MM-DD' stamp (DOC-15)" >&2
    missing=1
  fi
done

if [ "$missing" -ne 0 ]; then
  exit 1
fi
echo "staleness-gate: every docs/*.md and docs/audits/*.md carries a currency stamp — ok"
