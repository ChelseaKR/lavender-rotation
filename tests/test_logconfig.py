"""FIX-12: structured, local-only logging (kv default + opt-in JSON)."""

from __future__ import annotations

import json
import logging
import sys

import pytest
from pipeline.cli import main as cli_main
from pipeline.logconfig import (
    LOG_FORMATS,
    JsonFormatter,
    KeyValueFormatter,
    configure_logging,
    get_logger,
)


def _our_handlers(logger: logging.Logger) -> list[logging.Handler]:
    """Handlers configure_logging() itself attached (ignores pytest's own caplog
    handlers, which pytest attaches to any non-propagating logger like ``lavender``)."""
    return [
        h for h in logger.handlers if isinstance(h.formatter, KeyValueFormatter | JsonFormatter)
    ]


def test_configure_logging_attaches_one_stderr_handler() -> None:
    root = configure_logging()
    assert root.name == "lavender"
    ours = _our_handlers(root)
    assert len(ours) == 1
    handler = ours[0]
    assert isinstance(handler, logging.StreamHandler)
    assert not isinstance(handler, logging.FileHandler)  # stderr, not a file on disk
    # Bound to *a* stderr-like stream — not asserting identity with the current
    # sys.stderr, since pytest's own output capturing swaps that object out
    # from under a handler created once and reused across the whole session.
    assert hasattr(handler.stream, "write")


def test_configure_logging_is_idempotent() -> None:
    first = configure_logging()
    second = configure_logging()
    assert second is first
    assert len(_our_handlers(second)) == 1  # no duplicate handler added on a second call


def test_get_logger_is_under_the_lavender_namespace() -> None:
    log = get_logger("lavender.ingest")
    assert log.name == "lavender.ingest"
    assert log.parent is not None
    assert log.parent.name == "lavender"


def test_formatter_renders_key_value_pairs() -> None:
    formatter = KeyValueFormatter()
    record = logging.LogRecord(
        name="lavender.ingest",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="stage=%s event=%s",
        args=("fetch", "start"),
        exc_info=None,
    )
    line = formatter.format(record)
    assert "level=info" in line
    assert "logger=lavender.ingest" in line
    assert "msg='stage=fetch event=start'" in line


def test_formatter_includes_exception_info() -> None:
    formatter = KeyValueFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            name="lavender.ingest",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="something failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    line = formatter.format(record)
    assert "exc_info=" in line
    assert "ValueError" in line
    assert "boom" in line


def test_no_network_handlers_are_ever_configured() -> None:
    root = configure_logging()
    for handler in root.handlers:
        assert isinstance(handler, logging.StreamHandler)
        assert not hasattr(handler, "host")  # rules out HTTPHandler / SysLogHandler-style sinks


# --- opt-in JSON format (`--log-format json`, OBS Tier C follow-up) ---


def _record(msg: str, *, exc: bool = False) -> logging.LogRecord:
    return logging.LogRecord(
        name="lavender.ingest",
        level=logging.ERROR if exc else logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=sys.exc_info() if exc else None,
    )


def test_json_formatter_emits_one_json_object_with_expected_keys() -> None:
    line = JsonFormatter().format(_record("stage=fetch event=start"))
    payload = json.loads(line)
    assert payload["level"] == "info"
    assert payload["logger"] == "lavender.ingest"
    assert payload["msg"] == "stage=fetch event=start"
    assert payload["ts"].endswith("Z")
    assert "exc_info" not in payload  # only present when an exception is attached


def test_json_formatter_includes_exception_info() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record("something failed", exc=True)
    payload = json.loads(JsonFormatter().format(record))
    assert "ValueError" in payload["exc_info"]
    assert "boom" in payload["exc_info"]


def test_configure_logging_switches_format_without_duplicating_handlers() -> None:
    try:
        kv_root = configure_logging()
        before = len(_our_handlers(kv_root))
        json_root = configure_logging(log_format="json")
        assert json_root is kv_root
        ours = _our_handlers(json_root)
        assert len(ours) == before == 1  # formatter swapped in place, no second handler
        assert isinstance(ours[0].formatter, JsonFormatter)
        assert isinstance(ours[0], logging.StreamHandler)  # still stderr, still local-only
    finally:
        configure_logging()  # restore the kv default for the rest of the suite


def test_configure_logging_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="unknown log_format"):
        configure_logging(log_format="syslog")
    assert "syslog" not in LOG_FORMATS


def test_cli_log_format_flag_switches_to_json(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        assert cli_main(["--log-format", "json", "recommend", "--k", "1"]) == 0
        ours = _our_handlers(logging.getLogger("lavender"))
        assert len(ours) == 1
        assert isinstance(ours[0].formatter, JsonFormatter)
    finally:
        configure_logging()  # restore the kv default for the rest of the suite
    capsys.readouterr()  # drain the recommendation printout
