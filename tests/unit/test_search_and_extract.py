from __future__ import annotations

import json
from pathlib import Path

import httpx

from content_factory.research.extract import extract
from content_factory.research.fetch import CapturedContent
from content_factory.research.search import FixtureSearchProvider, SearxngSearchProvider

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "research"


def test_searxng_adapter_parses_json_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["format"] == "json"
        return httpx.Response(200, json={"results": [
            {"title": "Energy in Sweden", "url": "https://example.se/r", "content": "wind 21%", "engine": "duckduckgo"},  # noqa: E501
            {"title": "No URL entry", "content": "ignored"},
        ]})

    provider = SearxngSearchProvider("http://127.0.0.1:8083", transport=httpx.MockTransport(handler))  # noqa: E501
    results = provider.search("sweden wind share 2025")
    assert len(results) == 1 and results[0].url == "https://example.se/r"


def test_fixture_provider_matches_exact_then_overlap(tmp_path: Path) -> None:
    f = tmp_path / "search.json"
    f.write_text(json.dumps({
        "sweden wind share 2025": [{"title": "T1", "url": "https://a", "snippet": "s"}],
        "unrelated topic": [{"title": "T2", "url": "https://b", "snippet": "s"}],
    }))
    p = FixtureSearchProvider(f)
    assert p.search("sweden wind share 2025")[0].url == "https://a"
    assert p.search("wind share sweden")[0].url == "https://a"  # token overlap
    assert p.search("zebra stripes")== []


def test_article_extraction_from_fixture_html() -> None:
    html = (FIXTURES / "energimyndigheten_wind_2025.html").read_bytes()
    cap = CapturedContent("https://example.se/wind", "https://example.se/wind", 200, "text/html", html, "0" * 64, ())  # noqa: E501
    doc = extract(cap)
    assert doc.kind == "article"
    assert ("21" in doc.text and "vindkraft" in doc.text.lower()) or "wind" in doc.text.lower()
    assert "<script" not in doc.text.lower()
    assert doc.title
