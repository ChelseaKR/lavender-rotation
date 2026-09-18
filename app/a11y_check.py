"""A dependency-free, offline checker for the mechanical WCAG 2.2 AA subset.

This is the fallback a11y gate when a browser-based runner (pa11y/axe) is not
available in the environment. It is intentionally conservative — it only asserts
things that are unambiguously checkable from static HTML — and ``make a11y``
prefers pa11y when it is installed. Run as ``python -m app.a11y_check FILE...``;
exits non-zero on any violation.

**It reports what it examined, per rule family, on every run.** Until #139 it
printed ``a11y: 0 violations`` and nothing else, which is the same sentence over
a page with twelve interactive controls and over a page with none. The audited
artifact is the second kind: ``app/render.py`` emits no ``<button>``,
``<input>``, ``<select>``, ``<textarea>`` or ``<form>`` at all, so every rule
that only has something to say about a control ran over nothing and reported a
pass. Meanwhile ``app/dashboard.py`` — the primary UI — builds twelve
interactive widgets that Streamlit renders at run time and that no gate in this
repository reaches. That was disclosed in README conformance row 6 and in
``tests/test_e2e_a11y.py``'s docstring; neither is reachable from the gate's own
output, and a green line is where a reader meets this.

So three rules hold here, and they are the point of the file:

1. **A family that examined nothing is never a pass.** It reports
   ``not_applicable`` with a written reason, because "no findings" and "no
   inputs" are otherwise byte-identical verdicts.
2. **Each family declares exactly one of a floor or a reason.** A floor says
   the audited page must have inputs here, so a render that stops emitting
   tables — or a selector that stops matching — fails instead of passing on
   nothing. A reason says having none is legitimate, and states why. Neither is
   optional and both cannot be set at once; ``tests/test_a11y.py`` holds the
   table to that.
3. **A floor is structural, never the current count.** Every floor here is 1.
   A floor equal to today's number is a hand-maintained counter, and this
   portfolio has repeatedly jammed its own merge queue on those.

This checker deliberately judges **no** interactive control. It has no
accessible-name, label-association or focus-order rule, because the surface it
audits has never contained a control to judge. That is a real limit rather than
a passing verdict, so it is printed as one — and it is self-limiting in the
direction that matters: a document that *does* contain a control is refused
outright (see ``_CONTROL_TAGS``), because a gate reporting on controls it cannot
judge is exactly the defect this file was rewritten to stop reporting.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

#: Tags that are interactive controls for the purpose of the census. Presence
#: of any of these is refused rather than judged — see the module docstring.
_CONTROL_TAGS = frozenset({"button", "input", "select", "textarea", "form", "details", "summary"})

#: Placeholder inside the ``controls`` family's reason, filled in with the
#: measured widget count at print time. It is a token rather than a substring of
#: English prose so that a reword of the sentence cannot silently stop the
#: substitution and leave the disclosure without its number.
_UNREACHED = "{unreached}"

#: Streamlit widget constructors, counted in ``app/dashboard.py`` so the gate's
#: disclosure carries a measured number rather than a sentence somebody typed.
_WIDGET_CALL = re.compile(
    r"\bst\.(?:slider|button|checkbox|text_input|text_area|number_input|selectbox"
    r"|multiselect|radio|toggle|file_uploader|download_button|expander|form)\s*\("
)


@dataclass(frozen=True)
class Family:
    """One rule family, and what it is entitled to say when it has no inputs.

    Exactly one of ``floor`` and ``empty_reason`` is set. ``floor`` means the
    audited page must have inputs here and an empty family is a failure;
    ``empty_reason`` means an empty family is legitimate and says why. The
    either/or is asserted by the suite, so a family added without deciding the
    question cannot slip through as a silent pass.
    """

    name: str
    #: What the family judged, phrased to follow its own counts.
    judged: str
    floor: int | None = None
    empty_reason: str | None = None


#: The rule families, in report order. ``document`` is the whole-page family:
#: lang, viewport, a single h1, a main landmark and a skip link are properties
#: of the document itself, so its denominator is one document, always present.
FAMILIES: tuple[Family, ...] = (
    Family(
        "document",
        "documents — lang, viewport, exactly one <h1>, a <main> landmark and a skip link",
        floor=1,
    ),
    Family("headings", "headings — no level is skipped going deeper", floor=1),
    Family("links", "links — each has a non-empty href and accessible text", floor=1),
    Family("tables", "tables — each has a caption", floor=1),
    Family("table headers", "<th> — each carries a scope", floor=1),
    Family(
        "images",
        "images — each has an alt attribute",
        empty_reason=(
            "this page embeds no images, so alt text has nothing to judge here. "
            "The rule is exercised by tests/test_a11y.py rather than by this page"
        ),
    ),
    Family(
        "controls",
        "interactive controls",
        empty_reason=(
            "app/render.py emits no <button>, <input>, <select>, <textarea>, <form> "
            "or <details>, and this checker has no rule that judges one. "
            f"{_UNREACHED} in app/dashboard.py are built by Streamlit at run time, "
            "are not in this artifact, and no gate in this repository reaches them "
            "(issue #139)"
        ),
    ),
)


def unreached_widget_call_sites(dashboard: Path | None = None) -> int | None:
    """Interactive widget call sites in the Streamlit UI this gate cannot reach.

    Returns ``None`` — never ``0`` — when the source cannot be read, because a
    missing file must not print as "nothing is unreached". That is this
    portfolio's dominant defect (absence rendered as a value) and it would land
    here as a reassuring number.
    """
    path = dashboard if dashboard is not None else Path(__file__).with_name("dashboard.py")
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return len(_WIDGET_CALL.findall(source))


class _A11yParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        #: ``(family name, message)``. The family is what lets the census
        #: attribute a finding; ``check_html`` still returns bare messages.
        self.findings: list[tuple[str, str]] = []
        self.html_lang = False
        self.has_viewport = False
        self.h1_count = 0
        self.heading_levels: list[int] = []
        self.has_main = False
        self.has_skip = False
        self._in_table = False
        self._table_has_caption = False
        self.tables = 0
        self.captions = 0
        self.links = 0
        self.images = 0
        self.ths = 0
        self.controls = 0
        self._open_a = False
        self._a_text = ""

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        # Split per-tag to keep cyclomatic complexity low (ruff C90); each helper
        # owns exactly one tag's bookkeeping.
        attrs = dict(attrs_list)
        if tag == "main" or attrs.get("role") == "main":
            self.has_main = True
        if tag in _CONTROL_TAGS:
            self._start_control(tag)
        handler = {
            "html": self._start_html,
            "meta": self._start_meta,
            "a": self._start_a,
            "img": self._start_img,
            "table": self._start_table,
            "caption": self._start_caption,
            "th": self._start_th,
        }.get(tag)
        if handler is not None:
            handler(attrs)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._start_heading(tag)

    def _start_control(self, tag: str) -> None:
        # Refused, not judged. This checker has no accessible-name, label or
        # focus-order rule, so counting a control as examined would report a
        # pass over an element nothing looked at.
        self.controls += 1
        self.findings.append(
            (
                "controls",
                f"<{tag}> is an interactive control and this checker has no rule that "
                "judges one — give it an accessible-name/label rule here, or audit "
                "this page with pa11y/axe instead",
            )
        )

    def _start_html(self, attrs: dict[str, str | None]) -> None:
        if attrs.get("lang"):
            self.html_lang = True

    def _start_meta(self, attrs: dict[str, str | None]) -> None:
        if attrs.get("name") == "viewport":
            self.has_viewport = True

    def _start_heading(self, tag: str) -> None:
        level = int(tag[1])
        self.heading_levels.append(level)
        if level == 1:
            self.h1_count += 1

    def _start_a(self, attrs: dict[str, str | None]) -> None:
        href = attrs.get("href") or ""
        if href.startswith("#") and ("skip" in (attrs.get("class") or "")):
            self.has_skip = True
        self._open_a = True
        self._a_text = ""
        self.links += 1
        if not href.strip():
            self.findings.append(("links", "anchor with empty href"))

    def _start_img(self, attrs: dict[str, str | None]) -> None:
        self.images += 1
        if not attrs.get("alt") and attrs.get("alt") != "":
            self.findings.append(("images", "img without alt attribute"))

    def _start_table(self, _attrs: dict[str, str | None]) -> None:
        self._in_table = True
        self.tables += 1
        self._table_has_caption = False

    def _start_caption(self, _attrs: dict[str, str | None]) -> None:
        self.captions += 1
        self._table_has_caption = True

    def _start_th(self, attrs: dict[str, str | None]) -> None:
        self.ths += 1
        if not attrs.get("scope"):
            self.findings.append(("table headers", "th without scope attribute"))

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            if self._open_a and not self._a_text.strip():
                self.findings.append(("links", "link with no accessible text"))
            self._open_a = False
        if tag == "table":
            if not self._table_has_caption:
                self.findings.append(("tables", "table without caption"))
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._open_a:
            self._a_text += data


@dataclass(frozen=True)
class FamilyResult:
    """What one family examined, and what it is therefore entitled to say."""

    family: Family
    examined: int
    violations: tuple[str, ...]

    @property
    def verdict(self) -> str:
        """``pass`` / ``fail`` / ``not_applicable`` — never a pass over nothing.

        A family with a floor that examined nothing FAILS: the page was
        supposed to have inputs here and does not, which is a broken render or
        a selector that stopped matching, not a clean scan. A family with a
        written reason reports ``not_applicable``, which is not a failure and
        never was — it is only distinguishable from a pass, which is the whole
        point.
        """
        if self.violations:
            return "fail"
        if self.examined == 0:
            return "not_applicable" if self.family.floor is None else "fail"
        return "pass"

    @property
    def line(self) -> str:
        """One reader-facing sentence, always carrying this family's own counts."""
        if self.verdict == "not_applicable":
            return f"0 of 0 {self.family.name} — {self.family.empty_reason or ''}"
        if self.examined == 0:
            return (
                f"0 {self.family.name} — this page must have at least "
                f"{self.family.floor}; a render that stops emitting them, or a "
                "selector that stops matching, otherwise reports the same empty "
                "scan as a page with nothing wrong"
            )
        counts = f"{self.examined} of {self.examined}"
        if self.violations:
            return f"{counts} {self.family.judged} — {len(self.violations)} finding(s)"
        return f"{counts} {self.family.judged}"


@dataclass(frozen=True)
class Report:
    """The audit of one document: its findings and what produced them."""

    families: tuple[FamilyResult, ...]

    @property
    def violations(self) -> list[str]:
        return [v for result in self.families for v in result.violations]

    @property
    def elements(self) -> int:
        """Elements examined across every family, the census denominator."""
        return sum(result.examined for result in self.families)

    @property
    def failed(self) -> tuple[FamilyResult, ...]:
        return tuple(result for result in self.families if result.verdict == "fail")

    def census(self, unreached: int | None = None) -> list[str]:
        """The per-family lines printed on every run, passing or failing.

        ``unreached`` is ``None`` when ``app/dashboard.py`` could not be read.
        It prints as a sentence saying so, never as a number — a count of zero
        unreached controls is precisely the false reassurance this gate exists
        to stop giving.
        """
        measured = (
            f"The {unreached} widget call sites"
            if unreached is not None
            else "Its widget call sites (not counted here: app/dashboard.py could not be read)"
        )
        return [
            f"  {result.verdict:<15} {result.family.name:<14} "
            f"{result.line.replace(_UNREACHED, measured)}"
            for result in self.families
        ]


def audit(html: str) -> Report:
    """Audit one document, carrying every family's denominator alongside its findings."""
    parser = _A11yParser()
    parser.feed(html)
    by_family: dict[str, list[str]] = {family.name: [] for family in FAMILIES}
    for name, message in parser.findings:
        by_family[name].append(message)

    # Document-level rules. These are properties of the page as a whole, so the
    # family's denominator is one document — it is never empty, and a page that
    # is missing all five is a failing document, not an unexamined one.
    document = by_family["document"]
    if not parser.html_lang:
        document.append("<html> missing lang attribute")
    if not parser.has_viewport:
        document.append("missing viewport meta (zoom/reflow)")
    if parser.h1_count != 1:
        document.append(f"expected exactly one <h1>, found {parser.h1_count}")
    if not parser.has_main:
        document.append("no <main> landmark")
    if not parser.has_skip:
        document.append("no skip link to main content")

    # Heading order: never skip a level going deeper.
    prev = 0
    for level in parser.heading_levels:
        if prev and level > prev + 1:
            by_family["headings"].append(f"heading level jumps from h{prev} to h{level}")
        prev = level

    examined = {
        "document": 1,
        "headings": len(parser.heading_levels),
        "links": parser.links,
        "tables": parser.tables,
        "table headers": parser.ths,
        "images": parser.images,
        "controls": parser.controls,
    }
    return Report(
        tuple(
            FamilyResult(family, examined[family.name], tuple(by_family[family.name]))
            for family in FAMILIES
        )
    )


def check_html(html: str) -> list[str]:
    """Return a list of accessibility violations (empty == passing).

    Unchanged in behavior and signature: it reports findings about the markup
    it was handed, which is what a fragment-level caller wants. It deliberately
    does NOT apply the per-family floors — a floor is a claim about the audited
    *page*, and applying it to a fragment would make every unit test that checks
    one rule fail on the six families it did not exercise. The floors are
    enforced by :func:`main`, which is the gate.
    """
    return audit(html).violations


def _audit_one(path: Path, unreached: int | None) -> int:
    """Audit one file, print its census, and return its exit code."""
    report = audit(path.read_text(encoding="utf-8"))
    failures = report.failed
    total = len(report.violations)
    headline = (
        f"a11y: {path} — {total} violation(s) over {report.elements} elements "
        f"in {len(FAMILIES)} rule families"
    )
    stream = sys.stderr if failures else sys.stdout
    print(headline, file=stream)
    for line in report.census(unreached):
        print(line, file=stream)
    if not failures:
        return 0
    for result in failures:
        for violation in result.violations:
            print(f"  ! {result.family.name}: {violation}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: python -m app.a11y_check FILE.html [FILE.html ...]", file=sys.stderr)
        return 2
    # Every path handed in is audited. Reading only argv[0] and reporting a
    # pass would be the same defect this file is about, one layer out: a gate
    # that reports success over inputs it never opened.
    unreached = unreached_widget_call_sites()
    codes = [_audit_one(Path(arg), unreached) for arg in args]
    return max(codes)


if __name__ == "__main__":
    raise SystemExit(main())
