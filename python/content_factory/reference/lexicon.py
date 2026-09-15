"""Everyday words to the closed vocabulary: the words half of reference retrieval."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from content_factory.schemas.reference import (
    ABSENT_INTERACTIONS,
    ContactTag,
    InteractionTag,
    Posture,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LEXICON_PATH = REPO_ROOT / "fixtures" / "reference" / "lexicon.v1.json"

FIELD_ORDER: tuple[str, ...] = ("interaction", "contact", "posture", "setting")
"""The FTS5 text columns in table order, which is also the bm25 weight order. Expansions are
ordered by it so a term's position in the answer says which column it will be searched in."""

_TERMS_BY_FIELD: dict[str, frozenset[str]] = {
    "interaction": frozenset(t.value for t in InteractionTag),
    "contact": frozenset(t.value for t in ContactTag),
    "posture": frozenset(p.value for p in Posture),
}
"""``setting`` is absent on purpose: it is free text on the clip, so it has no closed vocabulary to
check a term against."""

_ABSENT_VALUES: frozenset[str] = frozenset(t.value for t in ABSENT_INTERACTIONS)
_WORD_RE = re.compile(r"[a-z0-9_]+")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SETTING_RE = re.compile(r"^[a-z0-9_]+$")


class LexiconError(ValueError):
    """The lexicon file is wrong. The message always names the phrase or term at fault."""


@dataclass(frozen=True, slots=True)
class Expansion:
    """What a sentence was understood to mean, and what it was not."""

    text: str
    terms: tuple[str, ...] = ()
    matched_phrases: tuple[str, ...] = ()
    unmatched_words: tuple[str, ...] = ()
    absent_terms: tuple[str, ...] = ()

    def terms_for(self, field: str) -> tuple[str, ...]:
        """The bare terms this expansion asks of one field, in expansion order."""
        prefix = f"{field}:"
        return tuple(t[len(prefix) :] for t in self.terms if t.startswith(prefix))


@dataclass(frozen=True, slots=True)
class Lexicon:
    """A loaded, validated synonym table. Immutable, so two expansions cannot disagree."""

    version: str
    path: Path
    sha256: str
    min_word_length: int
    stopwords: frozenset[str]
    phrases: dict[tuple[str, ...], tuple[str, ...]]
    longest_phrase: int

    def targets(self, tokens: tuple[str, ...]) -> tuple[str, ...]:
        """The ``field:term`` pairs one already-normalised phrase maps to, empty when unknown."""
        return self.phrases.get(tokens, ())


def normalize(text: str) -> tuple[str, ...]:
    """Words of a query, folded the same way the FTS5 tokenizer folds a document."""
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    folded = folded.replace("\u2019", "").replace("'", "")
    return tuple(_WORD_RE.findall(folded))


def _field_and_term(target: str, phrase: str) -> tuple[str, str]:
    field, sep, term = target.partition(":")
    if not sep or field not in FIELD_ORDER:
        msg = f"phrase {phrase!r}: target {target!r} does not name a lexicon field"
        raise LexiconError(msg)
    allowed = _TERMS_BY_FIELD.get(field)
    if allowed is None:
        if not _SETTING_RE.match(term):
            msg = f"phrase {phrase!r}: setting term {term!r} is not a bare lowercase word"
            raise LexiconError(msg)
    elif term not in allowed:
        msg = f"phrase {phrase!r}: {field} term {term!r} is not in the contract"
        raise LexiconError(msg)
    return field, term


def _target_string(entry: object, phrase: str) -> str:
    """One target, accepted either as ``"interaction:hug"`` or as ``{field, term}``."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and set(entry) == {"field", "term"}:
        return f"{entry['field']}:{entry['term']}"
    msg = f"phrase {phrase!r}: target {entry!r} is neither 'field:term' nor {{field, term}}"
    raise LexiconError(msg)


def sort_terms(terms: set[str] | frozenset[str]) -> tuple[str, ...]:
    """``field:term`` pairs in FTS column order then alphabetical order. The one sort order."""
    return tuple(sorted(terms, key=lambda t: (FIELD_ORDER.index(t.partition(":")[0]), t)))


@lru_cache(maxsize=4)
def load_lexicon(path: Path | None = None) -> Lexicon:
    """Read, hash and validate the committed synonym table."""
    lexicon_path = LEXICON_PATH if path is None else Path(path)
    try:
        raw = lexicon_path.read_bytes()
    except OSError as exc:
        msg = f"{lexicon_path} cannot be read: {exc}"
        raise LexiconError(msg) from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"{lexicon_path} is not valid JSON: {exc}"
        raise LexiconError(msg) from exc
    if not isinstance(doc, dict):
        msg = f"{lexicon_path} must hold a JSON object"
        raise LexiconError(msg)

    version = doc.get("version", "")
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        msg = f"{lexicon_path}: version {version!r} is not major.minor.patch"
        raise LexiconError(msg)
    min_word_length = doc.get("min_word_length", 3)
    if not isinstance(min_word_length, int) or min_word_length < 1:
        msg = f"{lexicon_path}: min_word_length must be a positive integer"
        raise LexiconError(msg)
    stopwords = doc.get("stopwords", [])
    if not isinstance(stopwords, list) or any(not isinstance(w, str) for w in stopwords):
        msg = f"{lexicon_path}: stopwords must be a list of strings"
        raise LexiconError(msg)
    entries = doc.get("entries", [])
    if not isinstance(entries, list) or not entries:
        msg = f"{lexicon_path}: entries must be a non-empty list"
        raise LexiconError(msg)

    phrases: dict[tuple[str, ...], tuple[str, ...]] = {}
    previous = ""
    for entry in entries:
        if not isinstance(entry, dict):
            msg = f"{lexicon_path}: every entry must be an object, got {entry!r}"
            raise LexiconError(msg)
        phrase = entry.get("phrase", "")
        if not isinstance(phrase, str) or not phrase:
            msg = f"{lexicon_path}: an entry has no phrase"
            raise LexiconError(msg)
        if phrase < previous:
            msg = (
                f"{lexicon_path}: entries must be sorted by phrase, {phrase!r} follows {previous!r}"
            )
            raise LexiconError(msg)
        previous = phrase
        tokens = normalize(phrase)
        if " ".join(tokens) != phrase:
            msg = f"phrase {phrase!r} must already be normalised (got {' '.join(tokens)!r})"
            raise LexiconError(msg)
        if tokens in phrases:
            msg = f"phrase {phrase!r} appears twice"
            raise LexiconError(msg)
        targets = entry.get("targets", [])
        if not isinstance(targets, list) or not targets:
            msg = f"phrase {phrase!r} has no targets"
            raise LexiconError(msg)
        resolved: set[str] = set()
        for target in targets:
            field, term = _field_and_term(_target_string(target, phrase), phrase)
            resolved.add(f"{field}:{term}")
        if len(resolved) != len(targets):
            msg = f"phrase {phrase!r} repeats a target"
            raise LexiconError(msg)
        phrases[tokens] = sort_terms(resolved)

    stop = frozenset(stopwords)
    overlap = sorted(" ".join(t) for t in phrases if len(t) == 1 and t[0] in stop)
    if overlap:
        msg = f"{lexicon_path}: {overlap} are both a phrase and a stopword"
        raise LexiconError(msg)

    return Lexicon(
        version=version,
        path=lexicon_path,
        sha256=digest,
        min_word_length=min_word_length,
        stopwords=stop,
        phrases=phrases,
        longest_phrase=max(len(t) for t in phrases),
    )


def lexicon_sha256(path: Path | None = None) -> str:
    """The sha256 of the lexicon file's bytes, which is what a match set records."""
    return load_lexicon(path).sha256


@lru_cache(maxsize=4)
def _phrases_by_target(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """``field:term`` -> the everyday phrases that mean it, shortest first then alphabetical."""
    out: dict[str, list[str]] = {}
    for tokens, targets in load_lexicon(path).phrases.items():
        phrase = " ".join(tokens)
        for target in targets:
            out.setdefault(target, []).append(phrase)
    return {
        target: tuple(sorted(phrases, key=lambda p: (len(p.split()), len(p), p)))
        for target, phrases in out.items()
    }


def phrase_for(target: str, *, path: Path | None = None) -> str | None:
    """The plainest everyday phrase that expands to ``target`` (``"interaction:hug"`` -> "hug")."""
    found = _phrases_by_target(path).get(target)
    return found[0] if found else None


def expand(text: str, *, path: Path | None = None) -> Expansion:
    """Turn a sentence into ordered ``field:term`` pairs, leftover words and named gaps."""
    lexicon = load_lexicon(path)
    words = normalize(text)
    terms: set[str] = set()
    matched: set[str] = set()
    residue: list[str] = []

    position = 0
    while position < len(words):
        width = min(lexicon.longest_phrase, len(words) - position)
        while width > 0:
            window = words[position : position + width]
            found = lexicon.targets(window)
            if found:
                terms.update(found)
                matched.add(" ".join(window))
                position += width
                break
            width -= 1
        else:
            residue.append(words[position])
            position += 1

    unmatched = {
        word
        for word in residue
        if len(word) >= lexicon.min_word_length and word not in lexicon.stopwords
    }
    absent = {
        term.partition(":")[2]
        for term in terms
        if term.startswith("interaction:") and term.partition(":")[2] in _ABSENT_VALUES
    }
    return Expansion(
        text=text,
        terms=sort_terms(terms),
        matched_phrases=tuple(sorted(matched)),
        unmatched_words=tuple(sorted(unmatched)),
        absent_terms=tuple(sorted(absent)),
    )
