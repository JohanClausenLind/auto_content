"""SearchProvider (5.6): SearXNG adapter, deterministic fixture adapter, commercial slot."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str = ""
    published: str | None = None


class SearchProvider(ABC):
    @abstractmethod
    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]: ...


class SearxngSearchProvider(SearchProvider):
    """GET /search?format=json (the instance must enable the json format)."""

    def __init__(self, endpoint: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(base_url=endpoint, timeout=15, transport=transport)

    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        resp = self._http.get("/search", params={"q": query, "format": "json", "safesearch": 0})
        resp.raise_for_status()
        data = resp.json()
        out = [
            SearchResult(
                title=str(r.get("title") or ""),
                url=str(r.get("url") or ""),
                snippet=str(r.get("content") or ""),
                engine=str(r.get("engine") or ""),
                published=r.get("publishedDate"),
            )
            for r in data.get("results", [])
            if r.get("url")
        ]
        return out[:max_results]


class FixtureSearchProvider(SearchProvider):
    """Deterministic offline results from fixtures/research/search.json (query → results).
    Unknown queries fall back to best token overlap so tests never hit the network."""

    def __init__(self, fixture_file: Path) -> None:
        self._data: dict[str, list[dict]] = json.loads(fixture_file.read_text("utf-8"))

    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        rows = self._data.get(query)
        if rows is None:
            q_tokens = set(query.lower().split())

            def overlap(key: str) -> int:
                return len(q_tokens & set(key.lower().split()))

            best = max(self._data, key=overlap, default=None)
            rows = self._data.get(best or "", []) if best and overlap(best) > 0 else []
        return [
            SearchResult(
                title=str(r.get("title") or ""),
                url=str(r.get("url") or ""),
                snippet=str(r.get("snippet") or ""),
                engine=str(r.get("engine") or ""),
                published=r.get("published"),
            )
            for r in rows[:max_results]
        ]
