"""CLI context: where the API is and the session cookie for it (0600 file, never in the repo)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def context_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "content-factory"
    return base / "context.json"


@dataclass
class CliContext:
    api_url: str = "http://127.0.0.1:8000"
    session_cookie: str | None = None
    workspace_id: str | None = None

    @classmethod
    def load(cls) -> CliContext:
        p = context_path()
        if not p.exists():
            return cls()
        data = json.loads(p.read_text("utf-8"))
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def save(self) -> None:
        p = context_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), "utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(p)
