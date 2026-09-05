"""The reference library's words half: a sentence in, ranked clips out.

``lexicon`` turns everyday words into the closed vocabulary of
``content_factory.schemas.reference``; ``query`` turns an expansion into an FTS5 MATCH expression
and runs it against a built index. Nothing here reaches the network, a GPU or torch: retrieval is
FTS5 and bm25 from the standard library, which is also why a score is reproducible.
"""

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
