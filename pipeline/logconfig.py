"""Structured, local-only logging (FIX-12 — operability pass).

Every log record this project emits is written to **stderr only**, as either a flat
``key=value`` line (the default) or, opt-in via ``--log-format json``, one
JSON object per line carrying exactly the same fields. This is a deliberate
privacy invariant, not an incidental choice: the same no-egress posture
FIX-07's runtime guard enforces for API calls applies to logging too, so a
diagnostic channel can never become a telemetry channel. There is no
``logging.handlers.HTTPHandler``, ``SysLogHandler`` pointed at a remote host,
queue handler, or any other network sink configured anywhere in this module —
only a single :class:`logging.StreamHandler` on ``sys.stderr``.

The no-inference invariant extends into the log stream (OBS-11): no log call
site may emit identity vocabulary or per-artist identity data, in either
format — enforced by ``tests/test_log_privacy.py``.

Module loggers live under the ``lavender`` namespace (``lavender.ingest``,
``lavender.lastfm``, …) so a single handler on the ``lavender`` root logger
captures everything and callers can filter/silence a subsystem with the normal
``logging`` API. That namespace is load-bearing, not cosmetic: the stderr-only
guarantee above is implemented by configuring exactly that tree, so a logger
outside it is a logger outside the guarantee. ``tests/test_log_privacy.py``
asserts every logger name in the core packages sits under :data:`_NAMESPACE`
(one did not, ``wad.paths``, left behind by the rename in ADR 0012).
"""

from __future__ import annotations

import json
import logging
import sys
import time

_NAMESPACE = "lavender"
_CONFIGURED_LOGGERS: set[int] = set()
_OUR_HANDLERS: dict[int, logging.Handler] = {}

#: Supported ``--log-format`` values (both local-only, both stderr-only).
LOG_FORMATS = ("kv", "json")


class KeyValueFormatter(logging.Formatter):
    """Compact ``key=value`` line formatter — local-only, no external sink."""

    converter = staticmethod(time.gmtime)  # UTC, deterministic across machines

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        timestamp = self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ")
        parts = [
            f"ts={timestamp}",
            f"level={record.levelname.lower()}",
            f"logger={record.name}",
            f"msg={record.message!r}",
        ]
        if record.exc_info:
            parts.append(f"exc_info={self.formatException(record.exc_info)!r}")
        return " ".join(parts)


class JsonFormatter(logging.Formatter):
    """One JSON object per line — same fields as :class:`KeyValueFormatter`.

    Still local-only and stderr-only; the format changes, the egress posture
    does not. ``ts``/``level``/``logger``/``msg`` always, ``exc_info`` only
    when an exception is attached.
    """

    converter = staticmethod(time.gmtime)  # UTC, deterministic across machines

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        payload: dict[str, str] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.message,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: int = logging.INFO, log_format: str = "kv") -> logging.Logger:
    """Configure the ``lavender`` logger tree: one stderr handler, local-only.

    Idempotent per logger object — calling this more than once (``main()``
    plus a test, say) never duplicates handlers on the same logger. Repeat
    calls may switch ``log_format`` (``"kv"`` or ``"json"``); the existing
    handler's formatter is swapped in place.
    """
    if log_format not in LOG_FORMATS:
        raise ValueError(f"unknown log_format {log_format!r}; expected one of {LOG_FORMATS}")
    root = logging.getLogger(_NAMESPACE)
    root.setLevel(level)
    if id(root) not in _CONFIGURED_LOGGERS:
        handler = logging.StreamHandler(stream=sys.stderr)
        root.addHandler(handler)
        root.propagate = False
        _CONFIGURED_LOGGERS.add(id(root))
        _OUR_HANDLERS[id(root)] = handler
    formatter: logging.Formatter = JsonFormatter() if log_format == "json" else KeyValueFormatter()
    _OUR_HANDLERS[id(root)].setFormatter(formatter)
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a module logger under the project namespace, e.g. ``lavender.ingest``.

    A bare name is prefixed rather than returned as-is. Returning it unchanged
    would hand back a logger outside the configured tree — the ``wad.paths``
    defect — from the very helper whose job is to keep callers inside it.
    """
    if name == _NAMESPACE or name.startswith(f"{_NAMESPACE}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_NAMESPACE}.{name}")
