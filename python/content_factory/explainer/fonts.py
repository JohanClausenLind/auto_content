"""Text measurement from the pinned advance tables; mirrors packages/content-ui/src/text/fit.ts."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Literal

FontFamily = Literal["Inter", "Sora"]
_DATA = Path(__file__).with_name("data")
_FILES: dict[str, str] = {"Inter": "inter_widths.json", "Sora": "sora_widths.json"}
_LINE_BREAK = re.compile(r"\r\n?")

# Measured widths are multiplied by this so a line that "fits" never touches the frame edge.
FIT_SAFETY = 1.015
_FALLBACK_ADVANCE = 1000
_FALLBACK_LETTER = 560


@cache
def _table_file(family: str) -> dict[str, object]:
    with (_DATA / _FILES[family]).open(encoding="utf-8") as fh:
        return json.load(fh)


@cache
def _widths(family: str, weight: int) -> dict[str, int]:
    tables = _table_file(family)["widths"]
    assert isinstance(tables, dict)
    available = sorted(int(w) for w in tables)
    nearest = min(available, key=lambda w: (abs(w - weight), w))
    return {ch: int(adv) for ch, adv in tables[str(nearest)].items()}


def _fonts_version() -> str:
    versions = {str(_table_file(f)["fontsource_version"]) for f in _FILES}
    assert len(versions) == 1, f"advance tables disagree on the fontsource version: {versions}"
    return versions.pop()


FONTS_VERSION: str = _fonts_version()


def _advance(ch: str, table: dict[str, int]) -> int:
    adv = table.get(ch)
    if adv is not None:
        return adv
    cp = ord(ch)
    if 0x0300 <= cp <= 0x036F or cp == 0x200B:
        return 0
    if ch.isalpha() or ch.isnumeric():
        return table.get("n", _FALLBACK_LETTER)
    return _FALLBACK_ADVANCE


def measure_text(text: str, *, family: FontFamily, weight: int, font_px: float) -> float:
    """Unkerned advance sum in px; kerning is ignored, which only widens the estimate."""
    table = _widths(family, weight)
    units = sum(_advance(ch, table) for ch in text)
    return units / 1000 * font_px


def fits(text: str, width_px: float, *, family: FontFamily, weight: int, font_px: float) -> bool:
    measured = measure_text(text, family=family, weight=weight, font_px=font_px)
    return measured * FIT_SAFETY <= width_px


def wrap_text(
    text: str, max_width_px: float, *, family: FontFamily, weight: int, font_px: float
) -> list[str]:
    """Greedy word wrap; words wider than the frame are hard-broken by character."""

    def width(s: str) -> float:
        return measure_text(s, family=family, weight=weight, font_px=font_px) * FIT_SAFETY

    lines: list[str] = []
    for para in _LINE_BREAK.sub("\n", text).split("\n"):
        words = [w for w in para.split() if w]
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if width(candidate) <= max_width_px:
                current = candidate
                continue
            if current:
                lines.append(current)
            if width(word) <= max_width_px:
                current = word
                continue
            chunk = ""
            for ch in word:
                if width(chunk + ch) <= max_width_px or not chunk:
                    chunk += ch
                else:
                    lines.append(chunk)
                    chunk = ch
            current = chunk
        lines.append(current)
    return lines
