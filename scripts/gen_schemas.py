#!/usr/bin/env python3
"""Write (or check) the committed JSON Schemas for the CLI's ``--json`` documents.

The schemas are generated from `pipeline/jsonout.py` rather than hand-written
beside it, so a document shape and the schema describing it cannot drift apart
silently. `schemas/*.schema.json` is committed because an integrator reads it
from the repository, not from a Python import.

    python3 scripts/gen_schemas.py            # write
    python3 scripts/gen_schemas.py --check    # fail if a committed file is stale

`tests/test_jsonout.py` runs the same comparison, so `make verify` catches the
drift whether or not anyone runs this by hand.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.jsonout import SCHEMAS, render_schema  # noqa: E402

SCHEMA_DIR = ROOT / "schemas"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="compare instead of writing; exit 1 on drift"
    )
    args = parser.parse_args(argv)

    stale: list[str] = []
    for name in sorted(SCHEMAS):
        rendered = render_schema(name)
        path = SCHEMA_DIR / name
        if args.check:
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
            if current != rendered:
                stale.append(name)
            continue
        SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")

    if args.check:
        if stale:
            print(  # noqa: T201
                "stale committed schema(s): "
                + ", ".join(stale)
                + "; run `python3 scripts/gen_schemas.py` and commit the result",
                file=sys.stderr,
            )
            return 1
        print(f"schemas: {len(SCHEMAS)} committed file(s) match the definitions")  # noqa: T201
        return 0
    print(f"schemas: wrote {len(SCHEMAS)} file(s) to {SCHEMA_DIR}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
