"""Export JSON Schema 2020-12 for every registered contract + fixture instances for TS tests.

Usage: uv run python scripts/export_schemas.py [--check]
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCHEMA_OUT = REPO / "packages" / "content-schema-ts" / "schema"
FIXTURE_OUT = REPO / "fixtures" / "schema"


def main(argv: list[str]) -> int:
    from content_factory.schemas.fixtures import export_fixtures
    from content_factory.schemas.registry import export_json_schemas

    export_json_schemas(SCHEMA_OUT)
    export_fixtures(FIXTURE_OUT)
    if "--check" in argv:
        diff = subprocess.run(  # noqa: S603
            ["git", "status", "--porcelain", "--", str(SCHEMA_OUT), str(FIXTURE_OUT)],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        if diff.strip():
            print("Schema drift detected — regenerate and commit:\n" + diff, file=sys.stderr)
            return 1
    print(json.dumps({"schemas": len(list(SCHEMA_OUT.glob("*.json"))), "fixtures": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
