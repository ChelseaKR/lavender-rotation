"""Root-level pytest bootstrap.

FIX-12: ``pipeline.cache`` resolves its ``DEFAULT_DB_PATH`` (via
``pipeline.paths.default_db_path()``) at import time. This root ``conftest.py``
is loaded before ``tests/conftest.py`` (and therefore before anything imports
``pipeline.cache``), so setting ``LAVENDER_DATA_DIR`` here to an ephemeral,
per-session directory keeps the test suite from ever creating or touching the
real per-user data directory (``~/.local/share/lavender-rotation``,
``~/Library/Application Support/lavender-rotation``, …) on the machine running
the tests. It also suppresses the legacy-directory migration in
``pipeline.paths``, which never runs when the env var is set.
"""

from __future__ import annotations

import os
from tempfile import TemporaryDirectory

_TEST_DATA_DIR = TemporaryDirectory(prefix="lavender-test-data-")
os.environ.setdefault("LAVENDER_DATA_DIR", _TEST_DATA_DIR.name)
