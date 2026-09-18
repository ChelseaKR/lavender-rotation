"""The version this repository declares, held against the releases it actually has.

`pyproject.toml` is the single source of the version (REL-02). Until 2026-09-06 it
declared `0.1.0` while `git tag` listed nothing at all: a number published as though it
meant something, with no artifact behind it. Four documents said so in prose -- README,
CHANGELOG, SECURITY, CITATION -- but prose is not machine-readable, nothing checked that
the prose was still there, and `pipeline/http.py` and `docs/audits/vex.json` restated the
number in a form that could drift away from `pyproject.toml` without anything noticing.
`vex.json` in particular published `pkg:pypi/lavender-rotation@0.1.0`, a package URL that
resolves to nothing.

Two states are distinguished here, and only one of them is a defect:

* **No tags at all** -- legitimate, and where this repository stands. It passes, but only
  if the declared version says so in a form a tool can read (a PEP 440 ``.devN`` suffix,
  which sorts below the release it anticipates) *and* the documents a reader actually
  opens say so too. Silence is the finding.
* **Tags exist and none of them matches the declared version** -- a defect. The failure
  names the declared version and the newest tag, because "they disagree" is not
  actionable without both numbers.

The second branch is unreachable from this repository today, so it is exercised below
against synthetic tag lists, and it was proved end to end against a throwaway clone of
this repository carrying a real `v9.9.9` tag. It is never proved by tagging this
repository: cutting a tag is the maintainer's deliberate act (ADR 0010) and no automation
here may mint one.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path

import pytest
from pipeline import __version__ as PACKAGE_VERSION
from pipeline.http import USER_AGENT_BASE, build_user_agent

ROOT = Path(__file__).resolve().parent.parent

#: PEP 440 developmental release: `0.1.0.dev0` is not `0.1.0`, and every tool that
#: understands versions -- pip, packaging, an SBOM consumer -- reads the suffix as
#: "this is not the release it is named after".
DEV_SUFFIX = re.compile(r"\.dev\d+$")


def declared_version(root: Path = ROOT) -> str:
    """The one source of truth for this project's version."""

    with (root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def repository_tags(root: Path = ROOT) -> list[str]:
    """Every tag this checkout can see. Empty means "none visible", not "none exist"."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), "tag", "--list"],  # noqa: S607 - git is the tool
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_shallow(root: Path = ROOT) -> bool:
    """Whether this checkout was truncated -- in which case an empty tag list proves nothing."""

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "true"


def remote_tags(root: Path = ROOT) -> list[str] | None:
    """The tags the remote actually has, or None when the remote could not be asked.

    The local list is only as complete as the fetch that produced it: `actions/checkout`
    fetches no tags unless asked, so a gate reading `git tag` off a default checkout
    answers "none" whatever the truth is, and passes blind. `git ls-remote` is the
    authoritative answer, and it goes over the git protocol -- it costs no GitHub API
    quota at all.
    """

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [  # noqa: S607 - git is the tool
            "git",
            "-C",
            str(root),
            "ls-remote",
            "--tags",
            "--refs",
            "origin",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return None
    return sorted(
        line.rsplit("refs/tags/", 1)[-1]
        for line in result.stdout.splitlines()
        if "refs/tags/" in line
    )


def strip_v(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def newest_tag(tags: Sequence[str]) -> str:
    """The highest tag by its numeric components, ties broken by name.

    Deliberately not `--sort=-creatordate`: a tag re-cut later would then read as newer
    than the release it replaced, and a shallow fetch may not carry creator dates at all.
    """

    def key(tag: str) -> tuple[tuple[int, ...], str]:
        return tuple(int(part) for part in re.findall(r"\d+", strip_v(tag))), tag

    return max(tags, key=key)


def version_against_tags(declared: str, tags: Sequence[str]) -> str | None:
    """The finding, or None when the declared version is answerable to the tags."""

    if not tags:
        return None
    if any(strip_v(tag) == declared for tag in tags):
        return None
    return (
        f"pyproject.toml declares version {declared!r}, and none of the {len(tags)} tag(s) in "
        f"this repository matches it. The newest tag is {newest_tag(tags)!r}. Either a release "
        "was cut without bumping the declared version, or the declared version has run ahead "
        "of what was released and should carry a PEP 440 '.devN' suffix until it is tagged."
    )


#: Where a reader is told, in prose, that nothing has been released. Each is required
#: while there are no tags, and each must be retired by the commit that cuts the first
#: tag -- so the same list gates both directions.
UNRELEASED_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("README.md", r"release-producing, unreleased"),
    ("CHANGELOG.md", r"There is no tagged release yet"),
    ("SECURITY.md", r"no git tag and no GitHub release exist"),
    ("CITATION.cff", r"no git tag or GitHub release exists yet"),
)


def unreleased_statement_findings(root: Path, tags: Sequence[str]) -> list[str]:
    findings = []
    for name, pattern in UNRELEASED_STATEMENTS:
        text = (root / name).read_text(encoding="utf-8")
        present = re.search(pattern, text) is not None
        if not tags and not present:
            findings.append(
                f"{name} no longer tells a reader that nothing has been released "
                f"(expected to match {pattern!r}), and no tag exists to make that true."
            )
        if tags and present:
            findings.append(
                f"{name} still says nothing has been released, but {newest_tag(tags)!r} exists."
            )
    return findings


def citation_release_date_findings(root: Path, tags: Sequence[str]) -> list[str]:
    """`date-released` names a release. Without a tag there is none to name."""

    text = (root / "CITATION.cff").read_text(encoding="utf-8")
    dated = re.search(r"^date-released:", text, re.MULTILINE) is not None
    if not tags and dated:
        return ["CITATION.cff declares date-released, but this repository has no tags."]
    return []


def vex_component_versions(root: Path) -> dict[str, str]:
    component = json.loads((root / "docs" / "audits" / "vex.json").read_text(encoding="utf-8"))
    component = component["metadata"]["component"]
    _, _, purl_version = str(component["bom-ref"]).partition("@")
    return {
        "docs/audits/vex.json metadata.component.version": str(component["version"]),
        "docs/audits/vex.json metadata.component.bom-ref": purl_version,
    }


def restated_versions(root: Path) -> dict[str, str]:
    """Every place outside `pyproject.toml` that writes the version down again.

    Two of these are unavoidable -- a CFF file and a CycloneDX document each carry their
    own version field -- so they are checked rather than derived. The third restatement,
    the outbound User-Agent, was removed instead: `pipeline/http.py` now builds it from
    the installed package metadata.
    """

    citation = (root / "CITATION.cff").read_text(encoding="utf-8")
    citation_version = re.search(r"^version:\s*\"?([^\"\s]+)\"?\s*$", citation, re.MULTILINE)
    security = (root / "SECURITY.md").read_text(encoding="utf-8")
    security_version = re.search(
        r"version `([^`]+)` as the in-development source version", security
    )
    found = {
        "CITATION.cff version": citation_version.group(1) if citation_version else "",
        "SECURITY.md supported-versions note": (
            security_version.group(1) if security_version else ""
        ),
    }
    found.update(vex_component_versions(root))
    return found


def restatement_findings(root: Path, declared: str) -> list[str]:
    return [
        f"{where} says {found!r}, but pyproject.toml declares {declared!r}."
        for where, found in restated_versions(root).items()
        if found != declared
    ]


DECLARED = declared_version()
TAGS = repository_tags()


# --- the repository as it actually is --------------------------------------------------


def test_the_declared_version_is_answerable_to_the_tags_that_exist() -> None:
    finding = version_against_tags(DECLARED, TAGS)
    assert finding is None, finding


def test_an_empty_tag_list_is_measured_rather_than_inherited_from_a_shallow_clone() -> None:
    """An unfetched ref namespace looks exactly like a project that never released.

    That is this repository's own dominant defect class -- an absence rendered as a value
    -- so it fails here instead of quietly taking the easy branch. CI checks out with
    `fetch-depth: 0`, which fetches `refs/tags/*` explicitly.
    """

    assert TAGS or not is_shallow(), (
        "this checkout is shallow and reports zero tags, which is indistinguishable from a "
        "repository that has never been released. Check out with fetch-depth: 0."
    )


def test_the_checkout_can_see_every_tag_the_remote_has() -> None:
    """The half the local tag list cannot prove about itself.

    A checkout that fetched no tags and a repository that has none are the same empty
    list, and the checks above would take the second reading and pass. The remote settles
    it. A skip here is a visible "not checked", not a pass: it means the remote could not
    be reached, which does not happen in CI.
    """

    published = remote_tags()
    if published is None:
        pytest.skip("the remote could not be reached; only this checkout's tag list is available")

    missing = sorted(set(published) - set(TAGS))
    assert not missing, (
        f"the remote has tag(s) this checkout cannot see: {missing}. A gate reading tags from "
        "this checkout would report 'never released' and pass. Check out with fetch-depth: 0."
    )

    finding = version_against_tags(DECLARED, published)
    assert finding is None, finding


def test_an_untagged_version_says_so_in_a_form_a_tool_can_read() -> None:
    """The prose below is for people; this is the half a machine can act on."""

    if TAGS:
        assert not DEV_SUFFIX.search(DECLARED), (
            f"{DECLARED!r} is a developmental version, but {newest_tag(TAGS)!r} is tagged."
        )
    else:
        assert DEV_SUFFIX.search(DECLARED), (
            f"pyproject.toml declares {DECLARED!r} and this repository has no tags, so nothing "
            "was ever built or signed under that number. A PEP 440 '.devN' suffix says that in "
            "a form pip, an SBOM consumer, and a dependency scanner all read correctly; prose "
            "in the README does not. Bump to the bare version in the commit that gets tagged."
        )


def test_every_document_that_restates_the_version_agrees_with_pyproject() -> None:
    findings = restatement_findings(ROOT, DECLARED)
    assert findings == [], findings


def test_the_outbound_user_agent_is_derived_rather_than_restated() -> None:
    """MusicBrainz is told a version number. It should be this project's real one."""

    assert PACKAGE_VERSION == DECLARED, (
        f"the installed package reports {PACKAGE_VERSION!r} but pyproject.toml declares "
        f"{DECLARED!r} -- re-run `make install` so the metadata matches the source tree."
    )
    assert f"lavender-rotation/{DECLARED}" == USER_AGENT_BASE
    assert build_user_agent("someone@example.org").startswith(f"lavender-rotation/{DECLARED} ")
    source = (ROOT / "pipeline" / "http.py").read_text(encoding="utf-8")
    assert DECLARED not in source, (
        "pipeline/http.py writes the version down again; derive it from `pipeline.__version__`."
    )


def test_the_documents_say_nothing_has_been_released_while_nothing_has() -> None:
    findings = unreleased_statement_findings(ROOT, TAGS) + citation_release_date_findings(
        ROOT, TAGS
    )
    assert findings == [], findings


# --- the branches this repository cannot reach ------------------------------------------


def test_a_tag_that_matches_the_declared_version_is_the_passing_case() -> None:
    assert version_against_tags("0.1.0", ["v0.1.0"]) is None
    assert version_against_tags("0.1.0", ["0.1.0"]) is None
    assert version_against_tags("0.2.0", ["v0.1.0", "v0.2.0"]) is None


@pytest.mark.parametrize(
    ("declared", "tags", "newest"),
    [
        ("0.1.0", ["v0.2.0"], "v0.2.0"),
        ("0.1.0.dev0", ["v0.1.0"], "v0.1.0"),
        ("0.3.0", ["v0.1.0", "v0.10.0", "v0.9.0"], "v0.10.0"),
    ],
)
def test_a_declared_version_no_tag_backs_is_a_failure_that_names_both(
    declared: str, tags: list[str], newest: str
) -> None:
    """The branch this repository cannot reach today. If it could not fail, it would not
    be a gate -- so it is driven here with tag lists this repository does not have."""

    finding = version_against_tags(declared, tags)
    assert finding is not None
    assert repr(declared) in finding
    assert repr(newest) in finding


def test_the_newest_tag_is_chosen_numerically_not_lexically() -> None:
    assert newest_tag(["v0.9.0", "v0.10.0"]) == "v0.10.0"
    assert newest_tag(["v1.0.0", "v0.10.0"]) == "v1.0.0"


# --- negative controls: each check is shown to bite on a sabotaged copy ------------------


@pytest.fixture
def sabotage(tmp_path: Path) -> Path:
    """A copy of the documents these checks read, so a mutation cannot touch the repo."""

    copy = tmp_path / "repo"
    (copy / "docs" / "audits").mkdir(parents=True)
    (copy / "pipeline").mkdir()
    for name in ("README.md", "CHANGELOG.md", "SECURITY.md", "CITATION.cff", "pyproject.toml"):
        shutil.copy(ROOT / name, copy / name)
    shutil.copy(ROOT / "docs" / "audits" / "vex.json", copy / "docs" / "audits" / "vex.json")
    shutil.copy(ROOT / "pipeline" / "http.py", copy / "pipeline" / "http.py")
    return copy


def test_the_clean_tree_produces_no_findings_at_all(sabotage: Path) -> None:
    """Without this, every negative control below could be passing for the wrong reason."""

    assert restatement_findings(sabotage, DECLARED) == []
    assert unreleased_statement_findings(sabotage, []) == []
    assert citation_release_date_findings(sabotage, []) == []


@pytest.mark.parametrize("document", [name for name, _ in UNRELEASED_STATEMENTS])
def test_deleting_a_release_stance_sentence_is_caught(sabotage: Path, document: str) -> None:
    pattern = dict(UNRELEASED_STATEMENTS)[document]
    path = sabotage / document
    before = path.read_text(encoding="utf-8")
    after = re.sub(pattern, "REMOVED", before)
    path.write_text(after, encoding="utf-8")

    assert after != before, f"the sabotage did not land: {pattern!r} never matched {document}"
    assert re.search(pattern, after) is None

    findings = unreleased_statement_findings(sabotage, [])
    assert any(document in finding for finding in findings), findings


def test_a_stance_sentence_left_standing_after_a_release_is_caught(sabotage: Path) -> None:
    """The other direction: the first tag has to retire these sentences."""

    findings = unreleased_statement_findings(sabotage, ["v0.1.0"])
    assert len(findings) == len(UNRELEASED_STATEMENTS)
    assert all("v0.1.0" in finding for finding in findings)


def test_a_citation_release_date_without_a_tag_is_caught(sabotage: Path) -> None:
    path = sabotage / "CITATION.cff"
    before = path.read_text(encoding="utf-8")
    after = before + "date-released: 2026-06-16\n"
    path.write_text(after, encoding="utf-8")

    assert re.search(r"^date-released:", after, re.MULTILINE), "the sabotage did not land"

    assert citation_release_date_findings(sabotage, []) != []
    assert citation_release_date_findings(sabotage, ["v0.1.0"]) == []


@pytest.mark.parametrize(
    ("document", "old", "new"),
    [
        ("CITATION.cff", f"version: {DECLARED}", "version: 9.9.9"),
        (
            "SECURITY.md",
            f"version `{DECLARED}` as the in-development",
            "version `9.9.9` as the in-development",
        ),
        ("docs/audits/vex.json", f'"version": "{DECLARED}"', '"version": "9.9.9"'),
        (
            "docs/audits/vex.json",
            f"pkg:pypi/lavender-rotation@{DECLARED}",
            "pkg:pypi/lavender-rotation@9.9.9",
        ),
    ],
)
def test_a_restated_version_drifting_from_pyproject_is_caught(
    sabotage: Path, document: str, old: str, new: str
) -> None:
    path = sabotage / document
    before = path.read_text(encoding="utf-8")
    after = before.replace(old, new, 1)
    path.write_text(after, encoding="utf-8")

    assert after != before, f"the sabotage did not land: {old!r} not found in {document}"
    assert "9.9.9" in after

    findings = restatement_findings(sabotage, DECLARED)
    assert any("9.9.9" in finding for finding in findings), findings
