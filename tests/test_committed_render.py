"""#71 — the committed `docs/audits/dashboard.html` must match what the code renders.

`docs/audits/` is the project's public evidence drawer. `.gitignore` names the
a11y dashboards as committed deliverables (unlike regenerable churn such as
`coverage.xml`), the README's Standards table links there for the accessibility
claim, and the audit notes name that render as what was audited. It is browsable
on GitHub.

Nothing regenerated it and nothing checked it. `make a11y` runs
`python -m app.build_static`, which **overwrites** the file and then audits the
fresh copy — destroying the evidence before looking at it — and
`tests/test_e2e_a11y.py` builds into a tmpdir, so the merge gate never touched
the committed copy either. On `main` it had drifted far enough to display three
MusicBrainz citations that do not locate a record, plus their matching `/edit`
links, under artist names next to gender claims: the exact defect PR #66 fixed,
still on public display in the committed artifact.

This is the issue's **Option A** — gate the artifact, keep the "browse the
audited page on GitHub" affordance. It runs in `make test` (stage 3), before
`make a11y` (stage 5) has a chance to overwrite anything, and the Makefile no
longer regenerates the committed file as a side effect of auditing it.
"""

from __future__ import annotations

from pathlib import Path

from app.build_static import DEFAULT_OUT, build

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMITTED = REPO_ROOT / DEFAULT_OUT

#: What to run when this test fails legitimately, i.e. the renderer or the
#: fixture changed on purpose. Named in the failure message so the fix is
#: obvious rather than archaeological.
REGENERATE = "make render   (or: python -m app.build_static)"


def _fresh(tmp_path: Path) -> str:
    return build(out=tmp_path / "dashboard.html").read_text(encoding="utf-8")


def test_the_render_is_deterministic(tmp_path: Path) -> None:
    """Byte-equality is only a fair gate if the renderer is reproducible.

    No timestamps, no ordering by anything unstable. If this ever fails, the
    equality check below must be relaxed to a structural comparison rather than
    the artifact being left ungated.
    """
    assert _fresh(tmp_path / "a") == _fresh(tmp_path / "b")


def test_committed_dashboard_matches_the_current_render(tmp_path: Path) -> None:
    """The committed artifact is what this code renders today — byte for byte."""
    assert COMMITTED.is_file(), f"{DEFAULT_OUT} is missing; run: {REGENERATE}"
    committed = COMMITTED.read_text(encoding="utf-8")
    assert committed == _fresh(tmp_path), (
        f"{DEFAULT_OUT} has drifted from what app/build_static.py renders. It is a "
        f"committed, publicly browsable deliverable, so a stale copy is a claim about "
        f"a build that no longer exists. Regenerate it and commit the result: {REGENERATE}"
    )


def test_committed_dashboard_cites_only_locatable_records() -> None:
    """The specific drift #71 caught: citations that locate no record.

    `tests/test_demo_citations.py` holds the *fixture* to this. The rendered
    artifact is a separate surface, and it was the one on public display, so it
    is checked separately rather than by inference from the fixture.
    """
    from pipeline.identity import citation_problem
    from pipeline.models import SourceKind

    html = COMMITTED.read_text(encoding="utf-8")
    problems = []
    for kind in (SourceKind.MUSICBRAINZ_GENDER, SourceKind.WIKIDATA_P21):
        prefix = (
            "https://musicbrainz.org/artist/"
            if kind is SourceKind.MUSICBRAINZ_GENDER
            else "https://www.wikidata.org/wiki/"
        )
        for chunk in html.split(f'href="{prefix}')[1:]:
            url = prefix + chunk.split('"')[0]
            url = url.removesuffix("/edit")  # the "fix at source" link
            problem = citation_problem(kind, url)
            if problem is not None:
                problems.append(f"{url} {problem}")
    assert not problems, (
        "the committed render shows citations that do not locate a record:\n  "
        + "\n  ".join(problems)
    )
