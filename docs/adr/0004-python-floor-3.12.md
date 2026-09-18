# 0004. Raise the Python floor to 3.12

Date: 2026-07-14

## Status

Accepted

## Context

ADR 0002 kept Python 3.10 as a short-term exception so the 3.9 security migration could remain
focused. It explicitly deferred a separate move to the portfolio's Python 3.12 floor. The project
has no deployment constraint that requires 3.10 or 3.11, local development already defaults to
3.13, and the locked runtime and application dependencies support 3.12 and 3.13.

The older floor also required mypy to skip Streamlit's bundled stubs because those stubs use
typing syntax unavailable to a 3.10 target. Keeping that workaround after the security migration
would preserve avoidable type-checking debt.

## Decision

Set `requires-python = ">=3.12"`, target Python 3.12 in ruff and mypy, and remove the Streamlit
stub override. CI verifies both supported minor versions, 3.12 and 3.13. The committed main
ruleset target requires those same two matrix contexts.

This decision supersedes ADR 0002 and the four-version matrix provision in ADR 0001. ADR 0001's
single-maintainer review posture and other branch controls remain in force.

## Consequences

- Python 3.10 and 3.11 users must upgrade before installing future builds.
- Streamlit and its transitive type information are checked under strict mypy instead of skipped.
- The supported-version declaration, local tooling, CI matrix, and required-check target agree.
- The earlier 3.9-to-3.10 security migration remains part of the historical changelog; this is a
  separate compatibility decision with its own validation.

## Follow-up (2026-08-28)

The Decision above says "and remove the Streamlit stub override", and Consequence 2
says Streamlit is "checked under strict mypy instead of skipped". Neither was
executed: `pyproject.toml` still carried

```toml
[[tool.mypy.overrides]]
module = ["streamlit.*"]
ignore_missing_imports = true
follow_imports = "skip"
```

with a comment stating that "we type-check our own code against py310" — twelve
lines below `python_version = "3.12"`. So ADR 0002's consequence ("the
`follow_imports = "skip"` workaround stays until a future >=3.12 migration"),
which this ADR was written to close, remained the operative reality.

The override is now removed and `mypy --strict` passes over all 42 source files
with Streamlit followed, which is what this ADR said would happen. The ADR's
stated decision and its consequences are true as of this date.
