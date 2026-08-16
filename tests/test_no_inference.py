"""THE guardrail test — written first, the centrepiece of the project.

The hard rule (README): *never infer an artist's gender or identity from name,
voice, image, genre, or any heuristic.* This test proves the inference path does
not exist, three ways:

1. **Vocabulary** — the permitted source kinds contain nothing name/voice/
   image/genre-derived, and there is no ``IdentityBasis`` for "inferred".
2. **Structure** — the resolver's inputs (``IdentityEvidence``) expose no name,
   image, audio, or genre field; its signature takes nothing forbidden.
3. **Code** — an AST scan proves no function in ``pipeline/`` reads a forbidden
   attribute (``genre``, ``tags``, ``voice``, ``image`` …) on any path that can
   reach an identity label.

Plus a behavioural proof: an artist with a feminine-coded name and a
"female vocalists" tag, but no identity evidence, resolves to UNKNOWN.

Mapped to the merge-blocking metric: *Inferred identity labels = 0*.

**Leg 3 was rewritten by #72.** It used to walk a hardcoded list of four
function names in one file. ``pipeline/identity.py`` defined seven functions, so
three were unscanned — including one added the week before — and nothing
asserted the list covered the module. A helper that mapped genre tags to a
gender, called from ``resolve_identity``'s unknown fallback, passed the scan.
The scan is now defined by three properties, each asserted here rather than
assumed:

* **Total.** Every ``def`` and ``async def`` in every ``pipeline/*.py`` module is
  walked. There is no allowlist to fall off.
* **Exemptions are named, justified, and live.** A function that legitimately
  handles tags (``ingest.enrich_artist`` builds an ``Artist``'s content tags) is
  listed in :data:`TAG_HANDLING_EXEMPTIONS` with a reason. A stale entry naming
  a function that no longer exists fails, so a hole cannot be pre-drilled.
* **Exempt is not unguarded.** Exempt functions are held to a *stricter* check:
  a taint walk asserting no value derived from a forbidden read ever reaches an
  identity constructor (``resolve_identity``, ``IdentityEvidence``, …).

And two structural facts the old scan assumed: ``recommender``/``app``/``export``
construct no identity objects at all (so an inference path cannot simply move
there), and the scan itself is proved to *fail* on the exact bypass #72
demonstrated.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from pipeline import identity
from pipeline.identity import IdentityEvidence, resolve_identity
from pipeline.models import (
    PERMITTED_SOURCES,
    Artist,
    Gender,
    IdentityBasis,
    SourceKind,
)

FORBIDDEN_TOKENS = frozenset(
    {
        "name",
        "voice",
        "vocal",
        "image",
        "photo",
        "picture",
        "face",
        "genre",
        "sound",
        "audio",
        "appearance",
        "pitch",
        "timbre",
        "guess",
        "infer",
        "predict",
        "heuristic",
    }
)

#: Attribute/variable names that must never be read on a path that can reach an
#: identity label. Deliberately narrow — a false positive here should be rare
#: enough that it deserves a comment, per #72's "the dangerous set is narrow
#: enough" note — and *exact*, so ``tags_by_artist`` is not swept up.
DANGEROUS_READS = frozenset(
    {"tags", "genre", "genres", "voice", "vocals", "image", "audio", "photo", "face", "timbre"}
)

#: Callables that turn values into identity claims. Nothing derived from a
#: forbidden read may appear in an argument to any of these.
IDENTITY_CONSTRUCTORS = frozenset(
    {
        "resolve_identity",
        "resolve_composition",
        "IdentityEvidence",
        "IdentityLabel",
        "BandComposition",
        "FrontPerson",
        "Source",
        "_map_value",
    }
)

#: Packages that read identity labels but must never construct one. #72 observed
#: this was true and out of scope; this turns "true" into "checked", so an
#: inference path cannot be introduced by moving it out of ``pipeline/``.
LABEL_READING_PACKAGES = ("recommender", "app", "export")

#: The one legitimate reason a scanned function reads a forbidden name: an
#: ``Artist``'s ``tags`` are *content/catalog* data that feeds the content-based
#: recommender, and they pass through ingest, the demo fixture, the Last.fm
#: parser, and (de)serialisation. Each entry is qualified ``module.function`` and
#: must name a function that exists. Being listed here is not a waiver — every
#: one of these is additionally held to :func:`tainted_identity_constructions`,
#: which is stricter than the blanket scan the rest of the module gets.
TAG_HANDLING_EXEMPTIONS: dict[str, str] = {
    "pipeline.demo.demo_source": "builds the fixture world's per-artist content tags",
    "pipeline.demo.demo_catalog": "carries content tags onto the fixture Artist records",
    "pipeline.demo.demo_profile": "carries content tags onto the fixture ListeningProfile",
    "pipeline.fixtures._build": "builds a generated fixture world's content tags",
    "pipeline.ingest.build_profile": "declares the profile's (initially empty) content tags",
    "pipeline.ingest.enrich_artist": "reads source.artist_tags() into Artist.tags (content only)",
    "pipeline.ingest.ingest": "collects per-artist content tags for the emitted profile",
    "pipeline.ingest.profile_from_cache": (
        "reads cached Artist.tags back into a rebuilt profile (content only); the "
        "identity on those same cached rows is never re-derived here, only re-read"
    ),
    "pipeline.lastfm.FixtureLastfm.__init__": "stores the fixture's per-artist content tags",
    "pipeline.lastfm.parse_top_tags": "parses Last.fm's top-tags payload (content signal only)",
    "pipeline.serde.artist_to_dict": "serialises Artist.tags to the cache row",
    "pipeline.serde.artist_from_dict": "deserialises Artist.tags from the cache row",
}


def test_no_source_kind_is_inference_derived() -> None:
    """No permitted source kind is derived from name/voice/image/genre."""
    for kind in SourceKind:
        haystack = f"{kind.name} {kind.value}".lower()
        for token in FORBIDDEN_TOKENS:
            assert token not in haystack, f"{kind!r} looks inference-derived"


def test_no_inferred_identity_basis_exists() -> None:
    """There is deliberately no 'inferred'/'guessed' identity basis."""
    bases = {b.value.lower() for b in IdentityBasis}
    assert bases == {"self-identified", "band-composition", "unknown"}
    for token in ("infer", "guess", "predict", "heuristic"):
        assert all(token not in b for b in bases)


def test_resolver_signature_takes_nothing_forbidden() -> None:
    """resolve_identity exposes no name/image/audio/genre parameter."""
    params = set(inspect.signature(resolve_identity).parameters)
    assert params == {"evidence"}, params
    # And the evidence record itself exposes no forbidden field. `is_local_correction`
    # (FIX-10) is a provenance flag, not a discriminator — it never participates in
    # gender mapping, only in how a citation is *labelled* once resolved.
    ev_fields = set(IdentityEvidence.__dataclass_fields__)
    assert ev_fields == {
        "kind",
        "value",
        "citation",
        "retrieved_at",
        "is_local_correction",
    }, ev_fields
    assert not (ev_fields & FORBIDDEN_TOKENS)


PIPELINE_DIR = Path(identity.__file__).resolve().parent
REPO_ROOT = PIPELINE_DIR.parent

_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef)  # async counts too (#72)


def _names_read(node: ast.AST) -> set[str]:
    """Every attribute/variable/parameter name mentioned under ``node``, lowered.

    Docstrings and comments are not code, so string constants are ignored — the
    scan looks at identifiers only.
    """
    found: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute):
            found.add(sub.attr.lower())
        elif isinstance(sub, ast.Name):
            found.add(sub.id.lower())
        elif isinstance(sub, ast.arg) or (isinstance(sub, ast.keyword) and sub.arg):
            found.add(str(sub.arg).lower())
    return found


def iter_functions(tree: ast.AST, module: str) -> list[tuple[str, ast.AST]]:
    """Every ``def``/``async def`` in ``tree``, qualified, including nested ones.

    Total by construction: there is no name list to fall off, which is the whole
    point of #72. A class body contributes ``Class.method``; a closure
    contributes ``outer.inner``.
    """
    out: list[tuple[str, ast.AST]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, _FUNC):
                out.append((f"{prefix}{child.name}", child))
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, f"{module}.")
    return out


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _binding(node: ast.AST) -> tuple[list[ast.expr], ast.AST | None]:
    """``(targets, value)`` for any node that binds names, else ``([], None)``."""
    if isinstance(node, ast.Assign):
        return list(node.targets), node.value
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return ([node.target], node.value) if node.value is not None else ([], None)
    if isinstance(node, ast.For):
        return [node.target], node.iter
    return [], None


def _taint_set(fn: ast.AST) -> set[str]:
    """Forbidden reads plus every name transitively bound from one, to a fixpoint."""
    tainted = set(DANGEROUS_READS)
    for _ in range(4):  # these functions are far shallower than four bindings deep
        before = len(tainted)
        for node in ast.walk(fn):
            targets, value = _binding(node)
            if value is None or not (_names_read(value) & tainted):
                continue
            for target in targets:
                tainted |= {sub.id.lower() for sub in ast.walk(target) if isinstance(sub, ast.Name)}
        if len(tainted) == before:
            break
    return tainted


def tainted_identity_constructions(fn: ast.AST) -> list[str]:
    """Values derived from a forbidden read that reach an identity constructor.

    The stricter check exempt functions are held to: being on the exemption list
    buys a function the right to *touch* content tags, never the right to feed
    them to :data:`IDENTITY_CONSTRUCTORS`.
    """
    tainted = _taint_set(fn)
    problems: list[str] = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and _call_name(node) in IDENTITY_CONSTRUCTORS):
            continue
        args: set[str] = set()
        for arg in node.args:
            args |= _names_read(arg)
        for kw in node.keywords:
            args |= _names_read(kw.value)
        leaked = args & tainted
        if leaked:
            problems.append(f"{_call_name(node)}() receives {sorted(leaked)}")
    return problems


def scan_source(source: str, module: str, exemptions: dict[str, str] | None = None) -> list[str]:
    """Run leg 3 over one module's source. Returns a list of violations.

    Extracted from the test body so the scan itself can be pointed at a
    synthetic module and *proved to fail* — #72 showed the previous scan
    returning PASS on a file that inferred gender from genre tags, and a scan
    that is never shown failing is not evidence of anything.
    """
    exempt = exemptions if exemptions is not None else TAG_HANDLING_EXEMPTIONS
    problems: list[str] = []
    for qualname, fn in iter_functions(ast.parse(source), module):
        leaked = _names_read(fn) & DANGEROUS_READS
        if not leaked:
            continue
        if qualname not in exempt:
            problems.append(f"{qualname} reads {sorted(leaked)} and is not an exempted tag handler")
            continue
        problems.extend(f"{qualname}: {p}" for p in tainted_identity_constructions(fn))
    return problems


def _pipeline_modules() -> list[tuple[str, Path]]:
    return [
        (f"pipeline.{path.stem}", path)
        for path in sorted(PIPELINE_DIR.glob("*.py"))
        if path.stem != "__init__"
    ]


def test_no_pipeline_function_reads_a_forbidden_attribute() -> None:
    """AST scan: nothing in ``pipeline/`` wires tags/genre/voice into identity.

    Every ``def`` and ``async def`` in every module, not a hardcoded list of
    four names in one file (#72). A function that legitimately handles content
    tags must be named in ``TAG_HANDLING_EXEMPTIONS`` with a reason, and is then
    held to the stricter no-tainted-value-into-an-identity-constructor check.
    """
    problems: list[str] = []
    for module, path in _pipeline_modules():
        problems.extend(scan_source(path.read_text(encoding="utf-8"), module))
    assert not problems, "no-inference scan violations:\n  " + "\n  ".join(problems)


def test_every_scanned_function_is_walked_and_no_exemption_is_stale() -> None:
    """The scan's *coverage* is asserted, not assumed — the #72 defect itself.

    Two directions. Every function in every scanned module is reachable by the
    walker (so nothing is silently outside it), and every declared exemption
    names a function that still exists with a non-empty reason (so a hole cannot
    be pre-drilled, or left behind by a rename).
    """
    walked: set[str] = set()
    for module, path in _pipeline_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = iter_functions(tree, module)
        walked |= {name for name, _ in found}
        # Independent count: every FunctionDef/AsyncFunctionDef node anywhere in
        # the file must be one the walker produced. Counted as a list, not a set,
        # because `@overload` stubs legitimately repeat a name.
        total = sum(1 for node in ast.walk(tree) if isinstance(node, _FUNC))
        assert total == len(found), f"{module}: the walker missed a function definition"
    stale = sorted(set(TAG_HANDLING_EXEMPTIONS) - walked)
    assert not stale, f"exemptions naming functions that no longer exist: {stale}"
    for qualname, reason in TAG_HANDLING_EXEMPTIONS.items():
        assert reason.strip(), f"{qualname} is exempted with no stated reason"


def test_identity_resolver_module_is_fully_scanned() -> None:
    """The specific regression: identity.py had 7 functions and 4 were scanned."""
    tree = ast.parse(Path(identity.__file__).read_text(encoding="utf-8"))
    defined = {name for name, _ in iter_functions(tree, "pipeline.identity")}
    assert len(defined) >= 7, defined
    # Not one of them is exempt: the resolver has no business touching tags.
    assert not (defined & set(TAG_HANDLING_EXEMPTIONS))
    assert (
        scan_source(Path(identity.__file__).read_text(encoding="utf-8"), "pipeline.identity") == []
    )


def test_the_scan_fails_on_the_genre_inference_bypass() -> None:
    """#72's exact bypass, which the old scan returned PASS on.

    A helper that maps genre tags to a gender, called from ``resolve_identity``'s
    unknown fallback. The old scan missed it because the helper's *name* was not
    in the four-name allowlist. Asserting the scan **fails** here is what makes
    the scan evidence rather than decoration.
    """
    bypass = (
        Path(identity.__file__).read_text(encoding="utf-8")
        + "\n\n"
        + "def _gender_from_tags(tags):\n"
        + "    if 'female vocalists' in tags:\n"
        + "        return Gender.WOMAN\n"
        + "    return None\n"
    )
    problems = scan_source(bypass, "pipeline.identity")
    assert problems, "the scan passed a module that infers gender from genre tags"
    assert any("_gender_from_tags" in p for p in problems), problems


def test_the_scan_sees_async_functions() -> None:
    """``ast.FunctionDef`` alone does not match ``async def`` — #72's one-token hole."""
    module = "async def _fetch_gender_from_image(image):\n    return image.face\n"
    assert [name for name, _ in iter_functions(ast.parse(module), "m")] == [
        "m._fetch_gender_from_image"
    ]
    assert scan_source(module, "m"), "an async inference path must not pass the scan"


def test_an_exempt_function_still_fails_if_a_tag_reaches_an_identity_call() -> None:
    """Exemption is not a waiver: the stricter check has teeth."""
    module = (
        "def enrich_artist(source, artist_id):\n"
        "    tags = source.artist_tags(artist_id)\n"
        "    guess = tags\n"
        "    return resolve_identity(guess)\n"
    )
    problems = scan_source(module, "m", exemptions={"m.enrich_artist": "content tags"})
    assert problems, "a tainted value reached resolve_identity() and the scan allowed it"
    assert any("resolve_identity()" in p for p in problems), problems


@pytest.mark.parametrize("package", LABEL_READING_PACKAGES)
def test_no_identity_object_is_constructed_outside_the_pipeline(package: str) -> None:
    """`recommender`/`app`/`export` read labels; they must never build one.

    #72 noted this was true and therefore out of scope. Left as an observation,
    it is also the obvious place for a future inference path to live — outside
    the only directory anything scans. Asserted instead.
    """
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in IDENTITY_CONSTRUCTORS:
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} {_call_name(node)}()"
                )
    assert not offenders, (
        f"{package}/ constructs identity objects; it must only read them:\n  "
        + "\n  ".join(offenders)
    )


def test_name_and_genre_do_not_influence_resolution() -> None:
    """An artist coded feminine by name + tags still resolves to UNKNOWN.

    No identity evidence is supplied, so the only thing that *could* sway the
    result is the (forbidden) name/genre — and it must not.
    """
    artist = Artist(
        artist_id="mbid-x",
        name="Florence Songbird",  # feminine-coded name — must be ignored
        tags=("female vocalists", "dream pop", "she"),  # coded tags — ignored
    )
    # The resolver takes evidence, not the artist; there is no way to pass the
    # name or tags in. With no evidence the answer is the first-class UNKNOWN.
    label = resolve_identity(evidence=[])
    assert label.gender is Gender.UNKNOWN
    assert label.basis is IdentityBasis.UNKNOWN
    assert label.sources == ()
    # The artist as constructed is also unknown by default.
    assert artist.identity.gender is Gender.UNKNOWN


def test_band_composition_evidence_cannot_set_individual_gender() -> None:
    """Lineup evidence alone never establishes a *person's* gender."""
    lineup_only = [
        IdentityEvidence(
            kind=SourceKind.DISCOGS_LINEUP,
            value="female",  # even if the raw value looks like a gender
            citation="https://www.discogs.com/artist/123",
            retrieved_at="2026-05-31",
        )
    ]
    label = resolve_identity(lineup_only)
    assert label.gender is Gender.UNKNOWN, "composition source must not set gender"


def test_permitted_sources_is_closed_and_documented() -> None:
    """The permitted set equals exactly the enum members — no hidden kinds."""
    assert frozenset(SourceKind) == PERMITTED_SOURCES


def test_assert_permitted_only_rejects_unknown_kind() -> None:
    """The explicit defensive guard raises on a non-permitted source kind."""
    from unittest.mock import patch

    from pipeline.identity import assert_permitted_only

    # A genuinely-permitted kind passes.
    assert_permitted_only(
        [IdentityEvidence(SourceKind.WIKIDATA_P21, "Q6581072", "c", "2026-05-31")]
    )
    # Simulate a future, non-permitted kind sneaking through by faking membership.
    bad = IdentityEvidence(SourceKind.WIKIDATA_P21, "x", "c", "2026-05-31")
    with (
        patch("pipeline.identity.INDIVIDUAL_IDENTITY_SOURCES", frozenset()),
        patch("pipeline.identity.BAND_COMPOSITION_SOURCES", frozenset()),
        pytest.raises(identity.InferenceForbiddenError),
    ):
        assert_permitted_only([bad])


def test_the_permitted_guard_runs_on_the_resolver_path() -> None:
    """#72: the guard had no caller outside its own test, so it guarded nothing.

    The test above proves its ``if`` works. This proves *the resolver invokes
    it* — a non-permitted kind now fails loudly at the two entry points where
    untrusted evidence becomes a label, instead of being silently skipped by the
    resolver's filter and returning UNKNOWN.
    """
    from unittest.mock import patch

    from pipeline.identity import resolve_composition
    from pipeline.models import FrontPerson

    evidence = [IdentityEvidence(SourceKind.WIKIDATA_P21, "Q6581072", "c", "2026-05-31")]
    lineup = [IdentityEvidence(SourceKind.DISCOGS_LINEUP, "lineup", "c", "2026-05-31")]
    with (
        patch("pipeline.identity.INDIVIDUAL_IDENTITY_SOURCES", frozenset()),
        patch("pipeline.identity.BAND_COMPOSITION_SOURCES", frozenset()),
    ):
        with pytest.raises(identity.InferenceForbiddenError):
            resolve_identity(evidence)
        with pytest.raises(identity.InferenceForbiddenError):
            resolve_composition([FrontPerson("Singer", "lead vocals")], lineup)


@pytest.mark.parametrize("token", sorted(FORBIDDEN_TOKENS))
def test_evidence_value_field_is_opaque_not_a_feature(token: str) -> None:
    """A forbidden token as a raw value never maps to a gender by itself."""
    ev = IdentityEvidence(
        kind=SourceKind.MUSICBRAINZ_GENDER,
        value=token,
        citation="mb://artist/abc",
        retrieved_at="2026-05-31",
    )
    # Tokens like "voice"/"genre" are not in the controlled vocab, so they map
    # to nothing — UNKNOWN. Only genuine sourced self-ID values ever map.
    assert resolve_identity([ev]).gender is Gender.UNKNOWN
