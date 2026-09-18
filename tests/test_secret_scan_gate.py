"""The secret scan's plumbing, which a run on a clean tree cannot demonstrate.

``scripts/secret-scan.sh`` falls back to grep when gitleaks is absent, and every
defect that fallback had was a way of reporting "0 findings" without having
looked. The script self-tests its patterns at run time; these tests cover the
plumbing around them.

Ported from the sibling repository that found and fixed the same defects, so a
fix made in one place cannot quietly drift back in the other.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET_SCAN = REPO_ROOT / "scripts" / "secret-scan.sh"


def _secret_scan_source() -> str:
    return SECRET_SCAN.read_text(encoding="utf-8")


def _secret_scan_code() -> str:
    """The script with whole-line comments removed.

    The header names each defect the script used to have, quoting the constructs
    that caused them (``|| true``, ``2>/dev/null``, "no tracked files yet"). An
    assertion that those strings are absent has to read the code alone, or the
    explanation of the fix reads as the defect itself.
    """
    return "\n".join(
        line
        for line in _secret_scan_source().splitlines()
        if not line.lstrip().startswith("#") or line.startswith("#!")
    )


def _secret_scan_patterns() -> list[str]:
    """The ``patterns=(...)`` array, unquoted the way the shell would unquote it."""
    block = re.search(r"^patterns=\(\n(.*?)^\)$", _secret_scan_source(), re.MULTILINE | re.DOTALL)
    assert block, "scripts/secret-scan.sh no longer defines a patterns=(...) array"
    patterns: list[str] = []
    for line in block.group(1).splitlines():
        if line.strip():
            patterns.extend(shlex.split(line, comments=True))
    assert patterns, "the secret scan's pattern list is empty"
    return patterns


#: One known positive per pattern, in the same order as the script's array.
#: Assembled from fragments for the same reason the script assembles its own:
#: the scan reads every tracked file, this one included, and a literal sample
#: here would make the gate report itself forever.
KNOWN_POSITIVES: tuple[str, ...] = (
    "AKIA" + "A" * 16,
    "-----BEGIN RSA PRIVATE " + "KEY" + "-----",
    "xox" + "b-0123456789abcdef",
    "AIza" + "b" * 35,
    "password" + '="correcthorsebatterystaple"',
)


def _as_python_regex(pattern: str) -> str:
    """Translate the one POSIX class these ERE patterns use into Python's dialect."""
    translated = pattern.replace("[[:space:]]", "[ \t\r\n\f\v]")
    assert "[[:" not in translated, (
        f"{pattern!r} uses a POSIX character class this translation does not "
        "handle, so the assertion below would not be testing what it appears to"
    )
    return translated


def test_secret_scan_patterns_match_known_positives() -> None:
    """Every pattern still matches the shape it exists to catch.

    The script checks this itself before scanning, which is the check that
    matters at run time. Repeating it here with independently written samples
    means a pattern and its shell sample cannot be weakened together and still
    look green.
    """
    patterns = _secret_scan_patterns()
    assert len(patterns) == len(KNOWN_POSITIVES), (
        f"the scan has {len(patterns)} patterns but this test knows "
        f"{len(KNOWN_POSITIVES)} samples; add the missing sample rather than "
        "leaving a pattern unproven"
    )
    for pattern, sample in zip(patterns, KNOWN_POSITIVES, strict=True):
        assert re.search(_as_python_regex(pattern), sample), (
            f"secret-scan pattern {pattern!r} no longer matches {sample!r}; it "
            "would contribute nothing to the scan and report 0 findings "
            "whatever the tree holds"
        )


def test_secret_scan_passes_every_pattern_after_dash_e() -> None:
    """The private-key pattern begins with ``-`` and is otherwise read as options.

    Without ``-e`` grep parsed it as an option bundle, printed "unrecognized
    option" to a discarded stderr and exited non-zero, which the caller read as
    "no match". That pattern had therefore never matched anything.
    """
    code = _secret_scan_code()
    invocations = re.findall(r"grep\s+(-[A-Za-z]+)\s+(?!-e\b)", code)
    assert not invocations, (
        f"a grep in secret-scan.sh takes its pattern positionally ({invocations}); "
        "a pattern beginning with `-` is then parsed as options and silently "
        "matches nothing"
    )
    assert code.count("grep -qIE -e") == 1, "the pattern self-check must survive"
    assert code.count("grep -InE -e") == 1, "the scanning grep must survive"


def test_secret_scan_refuses_a_tree_it_could_not_read() -> None:
    """A grep that could not read a file must not count as a file with no secret.

    grep exits non-zero both when it finds nothing and when it cannot read what
    it was pointed at, so the scanning grep's stderr is captured and any output
    on it fails the run.
    """
    code = _secret_scan_code()
    assert 'hits=$(xargs -0 grep -InE -e "$pat" <"$list" 2>"$errs" || true)' in code, (
        "the scanning grep no longer captures stderr to a file; a tracked file "
        "the scan could not read would count as a file with no secret in it"
    )
    assert '[ -s "$errs" ]' in code, (
        "nothing checks the captured stderr, so an unreadable tree still scans clean"
    )
    assert "2>/dev/null" not in code, (
        "secret-scan.sh discards a stream again; every defect this script had "
        "was an error being read as an absence of findings"
    )


def test_secret_scan_refuses_an_empty_or_unbuildable_file_list() -> None:
    """Both vacuous-pass doors the old script left open are shut."""
    code = _secret_scan_code()
    assert "no tracked files yet" not in code, (
        "the old `no tracked files yet — ok; exit 0` path is back; outside a git "
        "work tree it reported success having read nothing"
    )
    assert "refusing to report success" in code
    assert "refusing to report a clean scan of a tree it never read" in code
