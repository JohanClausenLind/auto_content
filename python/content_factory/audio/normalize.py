"""Text normalization for speech (15): spoken_text is derived from display_text deterministically
and stored separately. Pronunciation entries are applied as respellings for TTS."""

from __future__ import annotations

import re

from content_factory.schemas.audio import PronunciationEntry

NORMALIZATION_VERSION = "1"

_ABBREV = {
    "e.g.": "for example",
    "i.e.": "that is",
    "vs.": "versus",
    "etc.": "and so on",
    "%": " percent",
    "&": " and ",
    "€": " euros ",
    "$": " dollars ",
}


def normalize_for_speech(text: str, lexicon: tuple[PronunciationEntry, ...] = ()) -> str:
    out = text
    for entry in sorted(lexicon, key=lambda e: -len(e.term)):
        out = re.sub(rf"\b{re.escape(entry.term)}\b", entry.respelling, out)
    for k, v in _ABBREV.items():
        out = out.replace(k, v)
    out = re.sub(r"(\d)\s*percent", r"\1 percent", out)
    out = re.sub(r"[“”]", '"', out)
    out = re.sub(r"[‘’]", "'", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def tokenize_words(text: str) -> list[str]:
    """Words as a TTS/aligner sees them: punctuation stripped, hyphens kept, empty tokens dropped."""
    return [w for w in (re.sub(r"^[^\w]+|[^\w]+$", "", t) for t in text.split()) if w]
