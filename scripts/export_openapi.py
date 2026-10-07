#!/usr/bin/env python3
"""Write the REST API's OpenAPI document to ``web/openapi.json``: what the web interface's TypeScript types are generated
from (``cd web && npm run gen:api``).

    python scripts/export_openapi.py            # write it
    python scripts/export_openapi.py --check    # exit 1 if the file is out of date (what CI and the tests use)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agentlab.api.app import create_app
from agentlab.core.config import AgentLabConfig

TARGET = Path(__file__).resolve().parents[1] / "web" / "openapi.json"


def current() -> str:
    """The document as the file holds it: stable key order, two-space indent, one trailing newline."""
    app = create_app(config=AgentLabConfig(), serve_ui=False)
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="do not write; fail if the file differs")
    parser.add_argument("--output", type=Path, default=TARGET)
    args = parser.parse_args()
    text = current()
    if args.check:
        have = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if have != text:
            print(f"{args.output} is out of date; run: python scripts/export_openapi.py && (cd web && npm run gen:api)")
            return 1
        print(f"{args.output} is current")
        return 0
    args.output.write_text(text, encoding="utf-8")
    print(f"wrote {args.output} ({len(text):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
