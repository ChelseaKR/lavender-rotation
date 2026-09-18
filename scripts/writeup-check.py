#!/usr/bin/env python3
"""EXP-10 guard: the numbers claimed in docs/writeup/methods.md must match
docs/audits/eval-report.json (STANDARD: "the writeup is the artifact" — every
quantitative claim is regenerable by `make audit`, never hand-typed and stale).

Scans methods.md for its inline source annotations of the form::

    0.60 `[docs/audits/eval-report.json -> models.hybrid.precision_at_k]`

(the arrow is the unicode "->", written literally below to stay ASCII-clean)
and checks each claimed value against the live-regenerated JSON at the given
dotted path. Only annotations pointing at eval-report.json are checked here —
this is a light diff guard, not a full doc linter. Wired into `make audit`
(after `make eval` regenerates the file it checks against).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
METHODS_MD = REPO_ROOT / "docs" / "writeup" / "methods.md"
EVAL_REPORT = REPO_ROOT / "docs" / "audits" / "eval-report.json"

ARROW = "→"  # "->"

#: The value may sit immediately before the annotation ("0.60 `[... -> path]`")
#: or at the end of an inline code span ("`n_positives = 4` `[... -> path]`").
#: The second form was in the document from the start and this regex did not
#: match it, so two annotations — ``hybrid_beats_popularity`` and
#: ``n_positives`` — carried a source reference that read as gated and was
#: checked by nothing. :data:`ANY_ANNOTATION` below is what stops that class of
#: silent miss recurring.
ANNOTATION = re.compile(
    r"(?P<value>true|false|[-+]?\d+\.\d+|\d+)`?\s*"
    r"`\[docs/audits/eval-report\.json\s*" + ARROW + r"\s*(?P<path>[\w.]+)\]`"
)

#: Every annotation in the document, regardless of whether a value could be
#: read next to it. An annotation this finds and :data:`ANNOTATION` does not is
#: a claim wearing a citation that nothing verifies, which is worse than an
#: unannotated number: the reader is told it is checked.
ANY_ANNOTATION = re.compile(
    r"`\[docs/audits/eval-report\.json\s*" + ARROW + r"\s*(?P<path>[\w.]+)\]`"
)


def _resolve(data: object, dotted_path: str) -> object:
    for part in dotted_path.split("."):
        if not isinstance(data, dict) or part not in data:
            raise KeyError(f"path {dotted_path!r} does not resolve (stuck at {part!r})")
        data = data[part]
    return data


def _values_match(claimed: str, actual: object) -> bool:
    if claimed in ("true", "false"):
        return isinstance(actual, bool) and claimed == str(actual).lower()
    try:
        return (
            isinstance(actual, (int, float))
            and not isinstance(actual, bool)
            and float(claimed) == float(actual)
        )
    except ValueError:
        return False


def main() -> int:
    if not METHODS_MD.exists():
        print(f"writeup-check: {METHODS_MD} is missing", file=sys.stderr)
        return 1
    if not EVAL_REPORT.exists():
        print(
            f"writeup-check: {EVAL_REPORT} is missing — run `make eval` first",
            file=sys.stderr,
        )
        return 1

    report = json.loads(EVAL_REPORT.read_text())
    text = METHODS_MD.read_text()
    matches = list(ANNOTATION.finditer(text))

    if not matches:
        print("writeup-check: no eval-report.json annotations found in methods.md", file=sys.stderr)
        return 1

    # Every annotation must be one this script actually checks. Silently
    # skipping the ones whose value it could not parse turned a citation into
    # decoration: the number next to it was free to drift, and the reader had a
    # source reference telling them it could not.
    checked = {m.group("path") for m in matches}
    declared = {m.group("path") for m in ANY_ANNOTATION.finditer(text)}
    unchecked = sorted(declared - checked)
    if unchecked:
        print(
            "writeup-check: annotation(s) whose value this script cannot read, so "
            "nothing verifies them:",
            file=sys.stderr,
        )
        for path in unchecked:
            print(f"  - {path}", file=sys.stderr)
        print(
            "  Write the value immediately before the annotation, either bare "
            "(0.60 `[...]`) or at the end of a code span (`key = 4` `[...]`).",
            file=sys.stderr,
        )
        return 1

    failures = []
    for m in matches:
        claimed, path = m.group("value"), m.group("path")
        try:
            actual = _resolve(report, path)
        except KeyError as exc:
            failures.append(f"  {path}: {exc}")
            continue
        if not _values_match(claimed, actual):
            failures.append(
                f"  {path}: methods.md claims {claimed!r}, eval-report.json has {actual!r}"
            )

    if failures:
        print(
            f"writeup-check: {len(failures)} claim(s) in methods.md disagree with "
            f"the regenerated eval-report.json:",
            file=sys.stderr,
        )
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(f"writeup-check: {len(matches)} claim(s) in methods.md match eval-report.json — ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
