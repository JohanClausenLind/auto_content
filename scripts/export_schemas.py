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
            # `git status --porcelain` reports two very different problems here, and calling
            # both "drift" sends the operator to regenerate files that regenerating cannot fix.
            untracked, changed = [], []
            for line in diff.splitlines():
                if not line.strip():
                    continue
                (untracked if line.startswith("??") else changed).append(line[3:])
            if changed:
                print(
                    "Schema drift: generated contracts differ from the Pydantic source.\n"
                    "Run `just schemas` and commit the result.\n  " + "\n  ".join(changed),
                    file=sys.stderr,
                )
            if untracked:
                print(
                    "Generated contracts are not tracked, so this repo cannot regenerate its\n"
                    "contracts from a clean clone (`just schemas` and CI's schema-drift step\n"
                    "both depend on them). Regenerating will NOT fix this — commit them:\n  "
                    + "\n  ".join(untracked),
                    file=sys.stderr,
                )
            return 1
    print(json.dumps({"schemas": len(list(SCHEMA_OUT.glob("*.json"))), "fixtures": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
