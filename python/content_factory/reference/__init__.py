"""The reference library's words half: a sentence in, ranked clips out."""

from __future__ import annotations

from content_factory.reference.lexicon import (
    FIELD_ORDER,
    LEXICON_PATH,
    Expansion,
    Lexicon,
    LexiconError,
    expand,
    lexicon_sha256,
    load_lexicon,
    normalize,
)
from content_factory.reference.query import (
    RETRIEVER_VERSION,
    SCORE_SQL,
    build_match_expression,
    search,
)

__all__ = [
    "FIELD_ORDER",
    "LEXICON_PATH",
    "RETRIEVER_VERSION",
    "SCORE_SQL",
    "Expansion",
    "Lexicon",
    "LexiconError",
    "build_match_expression",
    "expand",
    "lexicon_sha256",
    "load_lexicon",
    "normalize",
    "search",
]
