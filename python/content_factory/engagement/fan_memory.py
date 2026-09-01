"""Per-fan conversation memory (14.1): the name they gave, running jokes, last topics, arc —
scoped per platform account, retained and deletable under the privacy policy."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FanMemory:
    fan_id: str
    platform: str
    given_name: str | None
    running_jokes: tuple[str, ...]
    last_topics: tuple[str, ...]
    message_count: int
    relationship_stage: str  # new | regular | vip


@dataclass
class FanMemoryStore:
    root: Path
    _cache: dict[str, dict] = field(default_factory=dict)

    def _path(self, platform: str, account: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", f"{platform}__{account}")
        return self.root / f"{safe}.json"

    def _load(self, platform: str, account: str) -> dict:
        key = f"{platform}|{account}"
        if key not in self._cache:
            p = self._path(platform, account)
            self._cache[key] = json.loads(p.read_text()) if p.exists() else {}
        return self._cache[key]

    def _save(self, platform: str, account: str) -> None:
        data = self._load(platform, account)
        p = self._path(platform, account)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        tmp.replace(p)

    def observe(self, platform: str, account: str, fan_id: str, text: str) -> FanMemory:
        data = self._load(platform, account)
        fan = data.setdefault(
            fan_id, {"message_count": 0, "given_name": None, "running_jokes": [], "last_topics": []}
        )
        fan["message_count"] += 1
        name_match = re.search(r"\b(?:i'?m|my name is|call me)\s+([A-Z][a-z]{1,20})\b", text)
        if name_match:
            fan["given_name"] = name_match.group(1)
        topics = [
            w
            for w in re.findall(r"[a-z]{5,}", text.lower())
            if w not in {"about", "there", "these", "those", "thanks"}
        ][:3]
        fan["last_topics"] = (topics + fan["last_topics"])[:6]
        self._save(platform, account)
        return self.get(platform, account, fan_id)

    def get(self, platform: str, account: str, fan_id: str) -> FanMemory:
        fan = self._load(platform, account).get(
            fan_id, {"message_count": 0, "given_name": None, "running_jokes": [], "last_topics": []}
        )
        count = fan["message_count"]
        stage = "vip" if count >= 25 else ("regular" if count >= 5 else "new")
        return FanMemory(
            fan_id,
            platform,
            fan.get("given_name"),
            tuple(fan.get("running_jokes", [])),
            tuple(fan.get("last_topics", [])),
            count,
            stage,
        )

    def delete_fan(self, platform: str, account: str, fan_id: str) -> bool:
        """Privacy: a fan's memory is deletable on request."""
        data = self._load(platform, account)
        if fan_id in data:
            del data[fan_id]
            self._save(platform, account)
            return True
        return False
