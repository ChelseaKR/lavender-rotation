#!/usr/bin/env bash
# i18n N/A declaration gate (INTERNATIONALIZATION-STANDARD §1).
# This repo is a declared i18n N/A candidate (single-user discovery/research
# tool; Streamlit output is operator-only — out-of-scope condition a). The
# standard requires the N/A decision be committed, never a silent skip: enforce
# that docs/I18N.md exists, declares `i18n status: N/A`, and gives a non-empty
# Reason. Merge-blocking, same as the standard's N/A-declaration AUTO-GATE.
set -euo pipefail

doc="docs/I18N.md"

if [ ! -f "$doc" ]; then
  echo "i18n-gate: $doc is missing — an i18n N/A repo MUST ship it (STANDARD §1)" >&2
  exit 1
fi

if ! grep -qE '^# i18n status: N/A[[:space:]]*$' "$doc"; then
  echo "i18n-gate: $doc lacks the '# i18n status: N/A' declaration (STANDARD §1)" >&2
  exit 1
fi

# Reason: must be present with non-whitespace content after the colon.
if ! grep -qE '^Reason:[[:space:]]*[^[:space:]].*$' "$doc"; then
  echo "i18n-gate: $doc has no non-empty 'Reason:' line (STANDARD §1)" >&2
  exit 1
fi

# Currency stamp. scripts/check-staleness.sh excludes this file from the DOC-15
# 'Last verified: YYYY-MM-DD' sweep, and says in its own comment that it does so
# because "scripts/i18n-gate.sh checks its 'Declared: YYYY-MM-DD - Reviewer: ...'
# line". It did not. The one governance doc excused from the currency gate was
# the one doc whose currency nothing checked, and the exclusion read as though
# something did. These two checks are that missing enforcement: an N/A
# declaration is a dated decision by a named person, or it is an assertion with
# nobody behind it.
if ! grep -qE '^Declared:[[:space:]]*[0-9]{4}-[0-9]{2}-[0-9]{2}([[:space:]]|$)' "$doc"; then
  echo "i18n-gate: $doc has no 'Declared: YYYY-MM-DD' date (DOC-15 substitute for" >&2
  echo "  the 'Last verified' stamp that check-staleness.sh exempts this file from)" >&2
  exit 1
fi

if ! grep -qE 'Reviewer:[[:space:]]*[^[:space:]]' "$doc"; then
  echo "i18n-gate: $doc names no 'Reviewer:' for its N/A declaration (DOC-15)" >&2
  exit 1
fi

echo "i18n-gate: docs/I18N.md declares i18n status N/A with a reason, a Declared date and a reviewer — ok"
