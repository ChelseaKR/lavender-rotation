"""No-identity-in-logs gate (OBS-11) — the no-inference invariant extends into
the log stream.

The diagnostic channel must never become an identity channel: log lines may
carry stage/timing/count/id data, never identity vocabulary or per-artist
identity values. Two legs, mirroring ``tests/test_no_inference.py``:

1. **Behavioral** — run the logging-heavy demo pipeline end to end with every
   ``lavender.*`` record captured, format each record with *both* shipped
   formatters (kv and JSON), and assert no identity vocabulary appears anywhere
   in the stream.
   *That capture is only as wide as the namespace.* It attaches a handler to the
   ``lavender`` root logger, so a module logging under some other name is not
   observed by this leg at all — which is exactly what happened:
   ``pipeline/paths.py`` logged to ``wad.paths`` from the rename until
   2026-08-28, outside the captured tree and outside the stderr-only handler
   ``configure_logging`` installs. A third leg below closes that by deriving
   every logger name from the source.
2. **Structural** — an AST scan over every logging call site in ``pipeline``,
   ``recommender``, ``app``, and ``export`` proves no call passes an
   identity-bearing name, attribute, or literal.

If a future change legitimately needs to log an identity-*adjacent* aggregate
(say, a coverage count), it must be reviewed and the vocabulary below adjusted
deliberately in the same PR — that friction is the point of the gate.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import app
import export
import pipeline
import recommender
from pipeline.cache import Cache
from pipeline.ingest import ingest
from pipeline.logconfig import JsonFormatter, KeyValueFormatter, configure_logging

#: Identity vocabulary that must never appear in the log stream. Word-boundary
#: matched, case-insensitive ("male" must not match "female" and vice versa —
#: both are listed explicitly).
IDENTITY_LOG_VOCABULARY = re.compile(
    r"\b("
    r"gender|pronoun|identity|identities|"
    r"woman|women|man|men|female|male|nonbinary|non-binary|"
    r"fronted|self-identified|band-composition|p21"
    r")\b",
    re.IGNORECASE,
)

#: Receivers that mark a call as a *logging* call (``st.info`` is a Streamlit
#: UI banner, not a log — deliberately excluded).
_LOGGER_NAMES = {"log", "logger", "_log", "_logger", "logging"}
_LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical", "log"}


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_demo_pipeline_log_stream_carries_no_identity_vocabulary(
    demo_user, source, enricher
) -> None:
    """Behavioral proof: a real cached ingest run logs no identity vocabulary.

    The demo world contains artists with sourced woman/nonbinary identities, so
    if any log call site leaked identity data this run would produce it.
    """
    root = configure_logging(level=logging.DEBUG)
    capture = _CaptureHandler()
    root.addHandler(capture)
    cache = Cache(":memory:")
    try:
        ingest(demo_user, source, enricher, cache=cache, fetched_at="2026-07-17")
    finally:
        cache.close()
        root.removeHandler(capture)
        configure_logging()  # restore INFO/kv defaults for the rest of the suite

    assert capture.records, "expected the ingest path to emit log records"
    for formatter in (KeyValueFormatter(), JsonFormatter()):
        for record in capture.records:
            line = formatter.format(record)
            match = IDENTITY_LOG_VOCABULARY.search(line)
            if match is not None:
                raise AssertionError(
                    f"identity vocabulary {match.group(0)!r} leaked into the log "
                    f"stream (OBS-11): {line!r}"
                )


def _core_files() -> list[Path]:
    roots = [
        Path(pipeline.__file__).parent,
        Path(recommender.__file__).parent,
        Path(app.__file__).parent,
        Path(export.__file__).parent,
    ]
    return [p for root in roots for p in root.rglob("*.py")]


def _is_logging_call(node: ast.Call) -> bool:
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr in _LOG_METHODS):
        return False
    receiver = func.value
    if isinstance(receiver, ast.Name):
        return receiver.id.lower() in _LOGGER_NAMES
    if isinstance(receiver, ast.Attribute):  # e.g. ``self.log.info(...)``
        return receiver.attr.lower() in _LOGGER_NAMES
    if isinstance(receiver, ast.Call):  # e.g. ``logging.getLogger(...).info(...)``
        inner = receiver.func
        return isinstance(inner, ast.Attribute) and inner.attr == "getLogger"
    return False


def test_no_logging_call_site_passes_identity_data() -> None:
    """Structural proof: no log call in the core packages names identity.

    Scans the *source text of every logging call* (format strings, keyword
    names, passed attributes/variables alike) so a violation is caught at
    review time, before it ever runs.
    """
    violations: list[str] = []
    for path in _core_files():
        source_text = path.read_text(encoding="utf-8")
        tree = ast.parse(source_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_logging_call(node):
                segment = ast.get_source_segment(source_text, node) or ""
                match = IDENTITY_LOG_VOCABULARY.search(segment)
                if match:
                    violations.append(
                        f"{path.name}:{node.lineno} logs identity vocabulary "
                        f"{match.group(0)!r}: {segment!r}"
                    )
    assert not violations, "identity data must never enter the log stream (OBS-11):\n" + "\n".join(
        violations
    )


def test_gate_scans_a_nonempty_call_population() -> None:
    """The structural gate is not vacuous: it actually finds logging calls."""
    count = 0
    for path in _core_files():
        source_text = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source_text)):
            if isinstance(node, ast.Call) and _is_logging_call(node):
                count += 1
    assert count >= 4, f"expected the core packages to contain logging calls, found {count}"


# --- Leg 3: the capture is only as wide as the namespace ---------------------


def _logger_names_in_source() -> dict[str, str]:
    """Every ``logging.getLogger("literal")`` in the core packages, by file.

    Derived from the AST rather than from a list, for the same reason
    ``tests/test_no_inference.py`` walks every function: a name list is a thing
    to fall off.
    """
    found: dict[str, str] = {}
    for path in _core_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "getLogger" or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found[arg.value] = f"{path.name}:{node.lineno}"
    return found


def test_every_logger_lives_under_the_configured_namespace() -> None:
    """A logger outside the tree is a logger outside the stderr-only guarantee.

    ``configure_logging`` attaches this project's single stderr handler to the
    ``lavender`` logger and sets ``propagate = False`` there. Records from a
    logger outside that tree never reach that handler: they skip the project
    formatter and fall through to whatever the *root* logger of the embedding
    process is configured with, which is the one place this module's docstring
    promises records never go. ``pipeline/paths.py`` logged to ``wad.paths``
    from the rename (ADR 0012) until this test existed, and neither leg above
    could see it — the behavioral leg captures on the ``lavender`` tree, and
    the structural leg reads call *arguments*, not logger names.
    """
    from pipeline.logconfig import _NAMESPACE

    stray = {
        name: where
        for name, where in _logger_names_in_source().items()
        if not (name == _NAMESPACE or name.startswith(f"{_NAMESPACE}."))
    }
    assert not stray, (
        "logger name(s) outside the configured namespace, so their records bypass "
        f"the project's only handler: {stray}"
    )


def test_get_logger_cannot_hand_back_a_logger_outside_the_tree() -> None:
    """The helper whose job is to keep callers inside the namespace must do it."""
    from pipeline.logconfig import _NAMESPACE, get_logger

    assert get_logger("paths").name == f"{_NAMESPACE}.paths"
    assert get_logger(f"{_NAMESPACE}.ingest").name == f"{_NAMESPACE}.ingest"
    assert get_logger(_NAMESPACE).name == _NAMESPACE


def test_a_stray_namespace_really_does_escape_the_configured_handler() -> None:
    """The consequence, demonstrated rather than asserted about.

    A record on the configured tree reaches the project's handler; the same
    record on a differently-named logger does not. Without this, the test above
    is a naming-convention check rather than a privacy one.
    """
    from pipeline.logconfig import _NAMESPACE, configure_logging

    root = configure_logging(level=logging.DEBUG)
    capture = _CaptureHandler()
    root.addHandler(capture)
    try:
        logging.getLogger(f"{_NAMESPACE}.somewhere").info("stage=test event=inside")
        logging.getLogger("wad.somewhere").info("stage=test event=outside")
    finally:
        root.removeHandler(capture)
        configure_logging()

    messages = [record.getMessage() for record in capture.records]
    assert "stage=test event=inside" in messages
    assert "stage=test event=outside" not in messages
