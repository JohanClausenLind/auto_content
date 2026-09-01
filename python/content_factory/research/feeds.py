"""RSS/Atom parsing with defusedxml (hostile XML fails closed)."""

from __future__ import annotations

from dataclasses import dataclass

from defusedxml import ElementTree as SafeET


class FeedError(Exception):
    pass


@dataclass(frozen=True)
class FeedItem:
    title: str
    url: str
    published: str | None
    summary: str


def parse_feed(data: bytes) -> list[FeedItem]:
    try:
        root = SafeET.fromstring(data)
    except Exception as exc:  # defusedxml raises on entity bombs, external entities, etc.
        raise FeedError(f"refused or invalid feed XML: {exc}") from exc
    items: list[FeedItem] = []
    tag = root.tag.lower()
    if tag.endswith("rss") or tag.endswith("rdf"):
        for item in root.iter("item"):
            items.append(
                FeedItem(
                    title=_text(item, "title"),
                    url=_text(item, "link"),
                    published=_text(item, "pubDate") or None,
                    summary=_text(item, "description")[:1000],
                )
            )
    elif tag.endswith("feed"):
        ns = "{http://www.w3.org/2005/Atom}"
        for entry in root.iter(f"{ns}entry"):
            link = ""
            for ln in entry.iter(f"{ns}link"):
                if ln.get("rel") in (None, "alternate"):
                    link = ln.get("href") or ""
                    break
            items.append(
                FeedItem(
                    title=_text(entry, f"{ns}title"),
                    url=link,
                    published=_text(entry, f"{ns}updated")
                    or _text(entry, f"{ns}published")
                    or None,
                    summary=_text(entry, f"{ns}summary")[:1000],
                )
            )
    else:
        raise FeedError(f"not a recognized feed root: {root.tag!r}")
    return [i for i in items if i.url]


def _text(node, name: str) -> str:
    el = node.find(name)
    return (el.text or "").strip() if el is not None and el.text else ""
