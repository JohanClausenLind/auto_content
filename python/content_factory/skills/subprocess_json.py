"""Shared parser for the JSON-summary-line-on-stdout contract the subprocess skills print."""

from __future__ import annotations

import json
from typing import Any


def last_json_line(text: str) -> dict[str, Any]:
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}
