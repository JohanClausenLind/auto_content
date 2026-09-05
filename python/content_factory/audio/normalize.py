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
    if lexicon:
        # ONE pass over the text, longest term first, and not one regex substitution per entry.
        # Sequential substitutions are not independent: with rules for both "kraft" and "kraftnät",
        # the long rule correctly produced "kraft-nate" and the short rule then rewrote the inside
        # of its own output to "KRAFT-nate". A single alternation can only match the original text,
        # so a respelling is never re-respelled. The alternation is ordered longest-first because
        # Python's `re` takes the leftmost alternative that matches at a position.
        by_term: dict[str, str] = {}
        for entry in lexicon:
            by_term.setdefault(entry.term, entry.respelling)
        alternation = "|".join(re.escape(term) for term in sorted(by_term, key=len, reverse=True))
        out = re.sub(rf"\b({alternation})\b", lambda m: by_term[m.group(1)], out)
    for k, v in _ABBREV.items():
        out = out.replace(k, v)
    out = re.sub(r"(\d)\s*percent", r"\1 percent", out)
    out = re.sub(r"[\u201c\u201d]", '"', out)
    out = re.sub(r"[\u2018\u2019]", "'", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def tokenize_words(text: str) -> list[str]:
    """Words as a TTS/aligner sees them: punctuation stripped, hyphens kept, empty tokens dropped."""  # noqa: E501
    return [w for w in (re.sub(r"^[^\w]+|[^\w]+$", "", t) for t in text.split()) if w]


# Number words a narrator says and an ASR writes back as digits. Everything up to "twenty" plus
# the tens and the scales, which is what covers a spoken figure.
_NUMBER_WORDS = frozenset(
    {
        "zero",
        "oh",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fourty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "million",
        "billion",
        "point",
    }
)

NUMBER_TOKEN = "\x00num\x00"  # noqa: S105 - a comparison sentinel, not a secret
"""What a run of number words or digits becomes in a script comparison. A private-use sentinel
rather than a word, so it can never collide with something the narrator actually said."""


def is_number_word(word: str) -> bool:
    """Is this token a number, however it is spelled? Digits, or a spoken number word."""
    stripped = word.lower().strip().replace(",", "").replace("%", "")
    if not stripped:
        return False
    if stripped.replace(".", "", 1).replace("-", "", 1).isdigit():
        return True
    return stripped in _NUMBER_WORDS


def spoken_word_shape(text: str) -> tuple[list[str], int]:
    """Words with each *run* of numbers collapsed to one sentinel, and how many runs there were.

    This exists because the alignment gate was failing beats **the model read correctly**. Measured
    on the wind_2024 fixture and the demo plan (2026-09-09):

    * the script said "40.8 terawatt-hours"; faster-whisper wrote "40 8 terawatt hours" -> 0.73
    * the script said "twelve hundred" and "four fifty"; faster-whisper wrote "1200" and "450"
      -> 0.73

    Both are the same figures rendered by two systems with different conventions, so comparing the
    renderings measured the aligner's number formatting rather than whether the model said the
    script. Masking them lets the words be compared as words.

    **It deliberately does not parse the numbers into values.** A spoken-number parser gets
    "twelve hundred and four fifty" wrong in at least two defensible ways (1254? 1200 and 450?),
    and a mis-parse would make the gate compare wrong values — passing a bad take or failing a
    good one, with a confident number attached either way. An ASR transcript is not reliable
    evidence of *which* figure was spoken; it is reliable evidence of how many were, and of the
    words around them. The run count is returned so a beat that dropped a figure entirely still
    fails, which is the failure that matters.
    """
    # Hyphens are split on both sides, because they are orthography and this is about words
    # spoken. Measured against the same aligner: a script saying "40.8 terawatt-hours" transcribes
    # as "40 8 terawatt hours", so masking the number alone still left "terawatt-hours" against
    # "terawatt hours" — a two-token difference in a three-token line, and the beat scored 0.40.
    # It also makes "twenty-one per cent" agree with "21 per cent", which is the same fault in the
    # commonest possible phrase.
    words = [part for word in tokenize_words(text.lower()) for part in word.split("-") if part]
    shape: list[str] = []
    runs = 0
    in_run = False
    for word in words:
        if is_number_word(word):
            if not in_run:
                shape.append(NUMBER_TOKEN)
                runs += 1
                in_run = True
            continue
        in_run = False
        shape.append(word)
    return shape, runs
