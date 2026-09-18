"""Every `lavender ...` invocation written in the docs must name a real command.

The failure this exists for: `README.md` and `CONTRIBUTING.md` both told a
reader to file a correction with `lavender corrections add --artist <id>
--source-kind <kind> --citation <url> --proposed <value> --note <why>`. There
has never been a `corrections add` subcommand. `corrections` takes only optional
flags and no positionals, and three of the flags named belong to a *different*
command, `pending-corrections add`. Anyone following the documented
fix-at-source flow — the flow this project points artists at when a claim about
them is wrong — got `unrecognized arguments: add`.

Nothing could have caught it. `make verify` type-checks the code and gates the
README's test-count sentence, and the argparse surface was built inside `main()`
so it only existed while an invocation was being parsed. `pipeline.cli` now
exposes `build_parser()`, and this walks it.

Deliberately structural rather than executable: it checks that the subcommand
exists and that every long flag belongs to it. It does not run anything and it
does not check that required arguments are present, because the docs
legitimately write `lavender ingest` in prose to name the command. A line that
passes is not thereby correct; a line that fails is certainly wrong, and that is
the whole class of error this catches.
"""

from __future__ import annotations

import argparse
import itertools
import re
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from pipeline.cli import build_parser

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Inline code spans and fenced code blocks. Prose that merely says the word
#: "lavender" is not an invocation — ADR 0012 explains the name in a sentence.
CODE_SPAN = re.compile(r"`([^`]+)`")  # inline spans may wrap a line
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)

INVOCATION = re.compile(r"(?<![\w/-])lavender\s+(?P<rest>[^\n`|]*)")

#: Dated working notes about unshipped proposals, and a history file that
#: records commands under names they no longer have. Neither claims to describe
#: today's CLI, and holding them to it would make the gate noise.
EXEMPT_PREFIXES = ("docs/ideation/", "CHANGELOG.md")


#: Directories that are never authored documentation.
_NOISE = {".venv", ".git", "node_modules", "__pycache__", "htmlcov", ".mypy_cache"}


def _tracked_markdown() -> list[Path]:
    """Every authored Markdown file, preferring git's own view of what is tracked.

    Falls back to a filesystem walk when git is unavailable or this is not a
    checkout (a source export, a mutation-testing copy). The fallback must never
    return *fewer* files than the git listing, or the gate would quietly shrink
    its own corpus: `test_the_docs_actually_contain_invocations_to_check`
    asserts the corpus is non-trivial for exactly that reason.
    """
    git = shutil.which("git")
    if git is not None:
        # S603: the argv is a constant list plus the absolute path
        # `shutil.which` resolved; nothing here is caller-supplied.
        listing = subprocess.run(  # noqa: S603
            [git, "ls-files", "*.md"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if listing.returncode == 0 and listing.stdout.strip():
            return [REPO_ROOT / name for name in listing.stdout.split()]
    return [
        path
        for path in sorted(REPO_ROOT.rglob("*.md"))
        if not _NOISE & set(path.relative_to(REPO_ROOT).parts)
    ]


def _code_regions(text: str) -> list[str]:
    # Fences first, then inline spans over what is left. Scanning both against
    # the same text would let the inline-span pattern run from one fence's
    # backticks to the next and stitch unrelated commands into one line.
    regions = [m.group(0) for m in FENCE.finditer(text)]
    outside_fences = FENCE.sub("", text)
    regions += [m.group(1).replace("\n", " ") for m in CODE_SPAN.finditer(outside_fences)]
    return regions


def _documented_invocations() -> list[tuple[str, str]]:
    """``(source file, argument string)`` for every `lavender ...` line in the docs."""
    found: list[tuple[str, str]] = []
    for path in _tracked_markdown():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(EXEMPT_PREFIXES):
            continue
        for region in _code_regions(path.read_text(encoding="utf-8")):
            for match in INVOCATION.finditer(region):
                rest = match.group("rest").split("#")[0].strip().rstrip(".,;:)")
                if rest:
                    found.append((rel, rest))
    return sorted(set(found))


DOCUMENTED = _documented_invocations()


def _subparsers(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def _long_options(parser: argparse.ArgumentParser) -> set[str]:
    return {opt for action in parser._actions for opt in action.option_strings}


def _positional_count(parser: argparse.ArgumentParser) -> int:
    """How many positional arguments this parser takes, subcommands excluded.

    Without this the checker could not describe a whole legitimate command
    shape: a command with positionals and no subcommands. It read the first
    positional as a subcommand and reported `lavender diff A B` -- a correct
    line -- as naming a subcommand that does not exist. That is the dangerous
    direction, because it is the one that gets "fixed" by editing the document
    to match the checker rather than the checker to match the command.
    """
    return sum(
        1
        for action in parser._actions
        if not action.option_strings and not isinstance(action, argparse._SubParsersAction)
    )


def _check_top_level(tokens: list[str], root: argparse.ArgumentParser) -> str | None:
    """Why this bare `lavender --flag` line is wrong, or ``None`` if it is fine.

    A top-level invocation names no subcommand: argparse answers ``lavender
    --help`` on the root parser alone. :func:`check_invocation` required the
    first token to be a subcommand, so it reported a correct line as naming a
    command that does not exist. Found by this gate firing on a documented
    `lavender --help`, which was right that the shape was unhandled and wrong
    that the line was broken. That is the more dangerous direction only because
    it is the one that gets "fixed" by editing the document.
    """
    root_options = _long_options(root)
    for token in tokens:
        if not token.startswith("--"):
            continue
        flag = token.split("=", 1)[0]
        if flag not in root_options:
            return f"{flag!r} is not a top-level lavender option"
    return None


def _check_flags(command: str, tokens: list[str], allowed: set[str]) -> str | None:
    for token in tokens:
        if not token.startswith("--"):
            continue
        flag = token.split("=", 1)[0]
        if flag not in allowed:
            return f"{flag!r} is not an option of `lavender {command}`"
    return None


def _check_positional_command(
    command: str,
    parser: argparse.ArgumentParser,
    rest: list[str],
    allowed: set[str],
) -> str | None:
    """A command that takes positionals rather than a subcommand."""
    positionals = _positional_count(parser)
    supplied = len(list(itertools.takewhile(lambda t: not t.startswith("-"), rest)))
    if supplied > positionals:
        return (
            f"`lavender {command}` takes {positionals} positional argument(s), "
            f"but the docs pass {supplied}"
        )
    return _check_flags(command, rest[supplied:], allowed)


def check_invocation(argv: str) -> str | None:
    """Why this documented invocation is wrong, or ``None`` if it is fine."""
    tokens = shlex.split(argv)
    root = build_parser()
    commands = _subparsers(root)
    command = tokens[0]
    if command.startswith("-"):
        return _check_top_level(tokens, root)
    if command not in commands:
        return f"{command!r} is not a lavender subcommand (have: {', '.join(sorted(commands))})"
    parser = commands[command]
    allowed = _long_options(parser) | _long_options(root)

    rest = tokens[1:]
    nested = _subparsers(parser)
    if rest and not rest[0].startswith("-"):
        if not nested:
            positionals = _positional_count(parser)
            if positionals:
                return _check_positional_command(command, parser, rest, allowed)
            return (
                f"{command!r} takes no subcommand, but the docs pass {rest[0]!r}. "
                f"Did they mean a different command?"
            )
        if rest[0] not in nested:
            return f"{command} has no {rest[0]!r} subcommand (have: {', '.join(sorted(nested))})"
        parser = nested[rest[0]]
        allowed |= _long_options(parser)
        rest = rest[1:]

    return _check_flags(command, rest, allowed)


def test_the_docs_actually_contain_invocations_to_check() -> None:
    """Guards the guard: an empty corpus makes every assertion below vacuous."""
    assert len(DOCUMENTED) >= 8, DOCUMENTED
    commands = {argv.split()[0] for _, argv in DOCUMENTED}
    assert {"ingest", "recommend", "refresh"} <= commands, commands


@pytest.mark.parametrize(
    ("where", "argv"),
    DOCUMENTED,
    ids=[f"{where}::{argv}" for where, argv in DOCUMENTED],
)
def test_every_documented_invocation_names_a_real_command(where: str, argv: str) -> None:
    problem = check_invocation(argv)
    assert problem is None, f"{where} documents `lavender {argv}`: {problem}"


def test_the_check_rejects_the_command_that_shipped_in_two_documents() -> None:
    """Shown failing, so a green run means something."""
    assert check_invocation("corrections add --artist x") is not None
    assert check_invocation("corrections --source-kind wikidata-p21") is not None
    assert check_invocation("recommend --not-a-flag") is not None
    assert check_invocation("definitely-not-a-command") is not None
    # And the true cases still pass, so it is not "reject everything".
    assert check_invocation("recommend --lens 1.0 --hide-sourced-men") is None
    assert check_invocation("pending-corrections add --artist x --source-kind y") is None
    assert check_invocation("ingest --user me") is None
    # Top-level invocations with no subcommand, both directions.
    assert check_invocation("--help") is None
    assert check_invocation("--definitely-not-a-top-level-flag") is not None
    # A command that takes positionals rather than a subcommand, both
    # directions. Added because a control on the arity branch stayed green:
    # once the docs were correct, no documented line reached it, so the branch
    # had no case that could fail.
    assert check_invocation("diff run-a run-b") is None
    assert check_invocation("diff run-a run-b --json") is None
    assert check_invocation("diff run-a run-b --allow-mixed") is None
    assert check_invocation("diff run-a run-b extra") is not None
    assert check_invocation("diff run-a run-b --not-a-flag") is not None
    # `runs` still resolves as a subcommand, not as a positional.
    assert check_invocation("runs show some-id") is None
    assert check_invocation("runs prune --keep 5") is None
    assert check_invocation("runs nonsense") is not None
