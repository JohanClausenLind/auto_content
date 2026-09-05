"""Ask the index in words: FTS5 MATCH for the sentence, SQL WHERE for everything else.

Two rules decide the whole module.

**Words go to FTS5, numbers go to SQL.** The sentence becomes one MATCH expression of
``field:"term"`` clauses joined with ``OR``, so a clip that carries three of the terms outranks a
clip that carries one. People count, affection, usage, source, pose format and duration are
``WHERE`` on the ``clip`` table instead. Putting a constraint in the MATCH would let bm25 trade it
away, and "two people" is not a preference.

**No embeddings.** ``pyproject.toml`` pins no torch, the corpus is a few thousand rows, and bm25
lets a test assert an exact number. The weights ``(0.0, 8.0, 6.0, 2.0, 1.0, 1.5)`` follow the FTS
column order: the tag columns decide the ranking, the caption is a tiebreaker, and the
unindexed id column is weighted zero. Ties break on source then clip id, so the same index and the
same query always return the same rows in the same order.

An index that is missing, empty or not yet built is not an error. It answers with an empty match
set that still carries the match expression and the expanded terms, because "the library has
nothing" is a useful answer and every lane has to keep running without it.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from content_factory.reference.lexicon import Expansion, expand, load_lexicon
from content_factory.schemas.reference import (
    ReferenceMatch,
    ReferenceMatchSet,
    ReferenceQuery,
)

RETRIEVER_VERSION = "1.0.0"
"""Bumped when the expression grammar, the weights or the ordering change, because a stored match
set has to say which retriever produced it."""

SCORE_SQL = "ROUND(-bm25(clip_fts, 0.0, 8.0, 6.0, 2.0, 1.0, 1.5), 4) AS score"
"""bm25 returns a negative number, smaller being better, so it is negated to rank descending.
Rounded to four places so a score is comparable across runs and assertable in a test."""

UNKNOWN_LIBRARY = "unknown_library"
"""Used when the index has no ``library`` row, which only happens for a hand-made test database."""

_SELECT_SQL = " ".join(
    [
        "SELECT clip_fts.clip_id, clip_fts.interaction, clip_fts.contact, clip_fts.caption,",
        SCORE_SQL,
        "FROM clip_fts JOIN clip ON clip.clip_id = clip_fts.clip_id",
    ]
)
_ORDER_SQL = "ORDER BY score DESC, clip.source ASC, clip_fts.clip_id ASC LIMIT ?"
_REQUIRED_TABLES = frozenset({"clip", "clip_fts"})


def fts_string(value: str) -> str:
    """One FTS5 string literal: double quoted, with any inner quote doubled."""
    return '"' + value.replace('"', '""') + '"'


def build_match_expression(expansion: Expansion) -> str:
    """The literal FTS5 MATCH string for one expansion, or "" when it asks for nothing.

    Guarantees: clauses are ``column:"string"`` joined with ``OR``, in the expansion's own order
    (FTS column order, then alphabetical), with one ``caption:"word"`` clause per surviving
    unmatched word. Every value is quoted, so a word can never be read as FTS5 syntax.
    """
    clauses = [
        f"{term.partition(':')[0]}:{fts_string(term.partition(':')[2])}" for term in expansion.terms
    ]
    clauses += [f"caption:{fts_string(word)}" for word in expansion.unmatched_words]
    return " OR ".join(clauses)


def _scalar_clauses(query: ReferenceQuery) -> tuple[list[str], list[object]]:
    """The non-word half of a query as SQL on the ``clip`` table, never as FTS terms."""
    clauses: list[str] = []
    params: list[object] = []
    if query.people_count is not None:
        clauses.append("clip.people_count = ?")
        params.append(query.people_count)
    if query.require_affection is not None:
        clauses.append("clip.affection = ?")
        params.append(query.require_affection.value)
    if query.require_usage is not None:
        clauses.append("clip.usage = ?")
        params.append(query.require_usage.value)
    if query.require_pose:
        # The indexed column, whose definition lives in the builder: 1 when a rig can actually be
        # aimed by this clip. A bounding box says where a person was, not how they were standing,
        # so bbox_only does not qualify. Admitting it made a search for drivable material return
        # television clips whose only geometry is a rectangle.
        clauses.append("clip.has_pose = 1")
    if query.sources:
        placeholders = ", ".join("?" for _ in query.sources)
        clauses.append(f"clip.source IN ({placeholders})")
        params.extend(source.value for source in query.sources)
    if query.min_duration_s is not None:
        clauses.append("clip.duration_s >= ?")
        params.append(query.min_duration_s)
    if query.max_duration_s is not None:
        clauses.append("clip.duration_s <= ?")
        params.append(query.max_duration_s)
    # Required tags are a filter, not a preference, so they are checked against the stored tag
    # text with a padded substring test rather than added to the MATCH where bm25 could trade
    # them away. The padding is what makes it a whole-token test: ' hands ' cannot hit 'hands_on'.
    for column, required in (
        ("interaction", query.require_interaction),
        ("contact", query.require_contact),
        ("posture", query.require_posture),
    ):
        for tag in required:
            clauses.append(f"instr(' ' || clip_fts.{column} || ' ', ?) > 0")
            params.append(f" {tag.value} ")
    return clauses, params


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(text.split())


def _empty_result(
    query: ReferenceQuery,
    expansion: Expansion,
    expression: str,
    library_id: str,
    lexicon_digest: str,
) -> ReferenceMatchSet:
    return ReferenceMatchSet(
        library_id=library_id,
        query=query,
        match_expression=expression,
        expanded_terms=expansion.terms,
        unmatched_words=expansion.unmatched_words,
        absent_terms=expansion.absent_terms,
        matches=(),
        retriever_version=RETRIEVER_VERSION,
        lexicon_sha256=lexicon_digest,
    )


def _library_id(conn: sqlite3.Connection) -> str:
    """The library's id, however the builder chose to store it.

    Two shapes exist and both are reasonable: one row holding the whole ``ReferenceLibrary``
    document, which keeps the contract intact, or key-value rows. Reading either keeps the query
    side from depending on that choice, which is not its business.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(library)")}
    if "doc" in columns:
        row = conn.execute("SELECT doc FROM library LIMIT 1").fetchone()
        if row and row[0]:
            try:
                return str(json.loads(row[0]).get("library_id") or UNKNOWN_LIBRARY)
            except (ValueError, AttributeError):
                return UNKNOWN_LIBRARY
        return UNKNOWN_LIBRARY
    if {"key", "value"} <= columns:
        row = conn.execute("SELECT value FROM library WHERE key = 'library_id'").fetchone()
        return str(row[0]) if row and row[0] else UNKNOWN_LIBRARY
    return UNKNOWN_LIBRARY


def search(
    index_path: Path | str,
    query: ReferenceQuery,
    *,
    lexicon_path: Path | None = None,
) -> ReferenceMatchSet:
    """Answer one ``ReferenceQuery`` from a built index.

    Guarantees: the index is opened read-only and never written; the words are searched with FTS5
    and ranked by bm25, the scalar constraints are SQL on the ``clip`` table; at most
    ``query.limit`` matches come back, ranked by score then source then clip id, so the same index
    and query always give the same answer; the returned set carries the match expression, the
    expanded terms, the unmatched words and the absent terms, so the caller can see what the
    sentence was understood to mean. A missing, unbuilt or empty index returns an empty match set
    rather than raising, and so does a sentence that expands to nothing searchable.
    """
    expansion = expand(query.text, path=lexicon_path)
    expression = build_match_expression(expansion)
    lexicon_digest = load_lexicon(lexicon_path).sha256
    path = Path(index_path)

    if not path.is_file():
        return _empty_result(query, expansion, expression, UNKNOWN_LIBRARY, lexicon_digest)

    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as conn:
        names = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not _REQUIRED_TABLES <= names:
            return _empty_result(query, expansion, expression, UNKNOWN_LIBRARY, lexicon_digest)
        library_id = _library_id(conn) if "library" in names else UNKNOWN_LIBRARY
        if not expression:
            return _empty_result(query, expansion, expression, library_id, lexicon_digest)

        clauses, params = _scalar_clauses(query)
        sql = " ".join(
            [_SELECT_SQL, "WHERE clip_fts MATCH ?", *(f"AND {c}" for c in clauses), _ORDER_SQL]
        )
        rows = conn.execute(sql, [expression, *params, query.limit]).fetchall()

    wanted_interaction = set(expansion.terms_for("interaction")) | {
        tag.value for tag in query.require_interaction
    }
    wanted_contact = set(expansion.terms_for("contact")) | {
        tag.value for tag in query.require_contact
    }
    matches = tuple(
        ReferenceMatch(
            clip_id=str(clip_id),
            rank=rank,
            score=float(score),
            matched_interaction=tuple(sorted(set(_tokens(interaction)) & wanted_interaction)),
            matched_contact=tuple(sorted(set(_tokens(contact)) & wanted_contact)),
            snippet=str(caption or "")[:400],
        )
        for rank, (clip_id, interaction, contact, caption, score) in enumerate(rows)
    )
    return ReferenceMatchSet(
        library_id=library_id,
        query=query,
        match_expression=expression,
        expanded_terms=expansion.terms,
        unmatched_words=expansion.unmatched_words,
        absent_terms=expansion.absent_terms,
        matches=matches,
        retriever_version=RETRIEVER_VERSION,
        lexicon_sha256=lexicon_digest,
    )
