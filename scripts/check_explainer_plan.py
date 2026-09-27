"""Check an explainer plan on disk against its contract and the planner prompt's rules.

    uv run python scripts/check_explainer_plan.py PLAN.json [--wps 2.4167]

Prints the JSON report from ``content_factory.explainer.check``. Exit status 1 when there is any
error, 0 otherwise; warnings do not fail.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from content_factory.explainer.check import WPS, check


def main() -> int:
    ap = argparse.ArgumentParser(description="Check an explainer plan.")
    ap.add_argument("plan", type=Path)
    ap.add_argument("--wps", type=float, default=WPS, help="spoken words per second")
    args = ap.parse_args()
    try:
        plan = json.loads(args.plan.read_text())
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "errors": [f"not JSON: {exc}"], "warnings": []}, indent=1))
        return 1
    report = check(plan, args.wps)
    print(json.dumps(report, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
