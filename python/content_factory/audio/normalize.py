"""Speech text normalization: spoken_text derives deterministically from display_text."""

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
        # One alternation, longest term first, not one substitution per entry: sequential rules
        # re-respell each other's output ("kraft" inside "kraft-nate"), and `re` takes the leftmost.
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
    """Words as a TTS/aligner sees them: punctuation stripped, hyphens kept."""
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


# British and American spelling folded on both sides of a comparison, or an American ASR fails a
# correct "colour" (0.74 against the 0.80 gate, journal 2026-09-09). Symmetric, so crude is safe.
_SPELLING_PAIRS: dict[str, str] = {
    # -our / -or, and the forms that keep or drop the u
    "colour": "color",
    "colours": "colors",
    "coloured": "colored",
    "colourful": "colorful",
    "honour": "honor",
    "honours": "honors",
    "honoured": "honored",
    "favour": "favor",
    "favours": "favors",
    "favoured": "favored",
    "favourite": "favorite",
    "favourites": "favorites",
    "behaviour": "behavior",
    "behaviours": "behaviors",
    "neighbour": "neighbor",
    "neighbours": "neighbors",
    "neighbourhood": "neighborhood",
    "harbour": "harbor",
    "harbours": "harbors",
    "labour": "labor",
    "labours": "labors",
    "vapour": "vapor",
    "vapours": "vapors",
    "odour": "odor",
    "rumour": "rumor",
    "rigour": "rigor",
    "splendour": "splendor",
    "armour": "armor",
    # -re / -er
    "centre": "center",
    "centres": "centers",
    "centred": "centered",
    "metre": "meter",
    "metres": "meters",
    "kilometre": "kilometer",
    "kilometres": "kilometers",
    "millimetre": "millimeter",
    "millimetres": "millimeters",
    "litre": "liter",
    "litres": "liters",
    "fibre": "fiber",
    "fibres": "fibers",
    "theatre": "theater",
    "theatres": "theaters",
    "calibre": "caliber",
    "sombre": "somber",
    "spectre": "specter",
    # doubled consonants
    "travelled": "traveled",
    "travelling": "traveling",
    "traveller": "traveler",
    "modelled": "modeled",
    "modelling": "modeling",
    "cancelled": "canceled",
    "cancelling": "canceling",
    "labelled": "labeled",
    "labelling": "labeling",
    "fuelled": "fueled",
    "marvellous": "marvelous",
    # single words with no rule behind them
    "grey": "gray",
    "greyish": "grayish",
    "aluminium": "aluminum",
    "sulphur": "sulfur",
    "sulphide": "sulfide",
    "mould": "mold",
    "moulds": "molds",
    "moult": "molt",
    "smoulder": "smolder",
    "plough": "plow",
    "draught": "draft",
    "kerb": "curb",
    "tyre": "tire",
    "tyres": "tires",
    "storey": "story",
    "storeys": "stories",
    "programme": "program",
    "programmes": "programs",
    "practise": "practice",
    "defence": "defense",
    "offence": "offense",
    "licence": "license",
    "pretence": "pretense",
    "cheque": "check",
    "cheques": "checks",
    "jewellery": "jewelry",
    "manoeuvre": "maneuver",
    "oesophagus": "esophagus",
    "foetus": "fetus",
    "anaemia": "anemia",
    "anaesthetic": "anesthetic",
    "palaeontology": "paleontology",
    "archaeology": "archeology",
    "ageing": "aging",
    "judgement": "judgment",
    "learnt": "learned",
    "spelt": "spelled",
    "burnt": "burned",
}

_SPELLING_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("isation", "ization"),
    ("isations", "izations"),
    ("ising", "izing"),
    ("ised", "ized"),
    ("ises", "izes"),
    ("ise", "ize"),
    ("yse", "yze"),
    ("ysed", "yzed"),
    ("ysing", "yzing"),
)
"""Regular folds, longest first. Applied only when the word is at least two letters longer than
the suffix, so "ise", "use" and "rise" are left alone."""


def fold_spelling(word: str) -> str:
    """One spelling for a word two dictionaries render differently. Idempotent."""
    exact = _SPELLING_PAIRS.get(word)
    if exact is not None:
        return exact
    for british, american in _SPELLING_SUFFIXES:
        if len(word) >= len(british) + 2 and word.endswith(british):
            return word[: -len(british)] + american
    return word


def spoken_word_shape(text: str) -> tuple[list[str], int]:
    """Words with each *run* of numbers collapsed to one sentinel, and how many runs there were."""
    # Hyphens split on both sides: the aligner writes "terawatt hours" for "terawatt-hours", and
    # masking the number alone still failed the beat (journal 2026-09).
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
        shape.append(fold_spelling(word))
    return shape, runs
