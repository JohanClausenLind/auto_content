"""Originality and Policy Engine (2.10): multimodal fingerprints + typed, explainable decisions.

Compared against the operator's OWN accounts/workspaces/content families (one tenant). A model can
never override a blocking decision; thresholds are data, the comparison is deterministic."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from PIL import Image


class OriginalityVerdict(StrEnum):
    original = "ORIGINAL"
    acceptable_adaptation = "ACCEPTABLE_ADAPTATION"
    needs_differentiation = "NEEDS_DIFFERENTIATION"
    too_similar = "TOO_SIMILAR"
    mass_production_risk = "MASS_PRODUCTION_RISK"


BLOCKING = {OriginalityVerdict.too_similar, OriginalityVerdict.mass_production_risk}

_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset(
    "a an the of to in and or is are was were be been on for with that this it as at by from".split()
)


def _normalize(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    words = _normalize(text)
    if len(words) < n:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dhash(image: Image.Image, size: int = 8) -> int:
    grey = image.convert("L").resize((size + 1, size))
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (1 if grey.getpixel((x, y)) > grey.getpixel((x + 1, y)) else 0)
    return bits


def hamming(a: int, b: int, bits: int = 64) -> int:
    return bin((a ^ b) & ((1 << bits) - 1)).count("1")


_FRAME_WORDS = frozenset(
    "in on at of to for with about roughly doubled supplied drove things three since that this "
    "has have had is are was were be been and or not it its their there here when what how why "
    "one two four five percent share more less new better cheaper good hope your day you".split()
)


def structural_shingles(text: str, n: int = 4) -> set[tuple[str, ...]]:
    """Shingles over the sentence FRAME: content words collapse to a placeholder, so swapping
    nouns ("Sweden"→"Norway", "wind"→"hydro") leaves the fingerprint intact."""
    words = [w if w in _FRAME_WORDS or w in _STOP else "*" for w in _WORD.findall(text.lower())]
    if len(words) < n:
        return {tuple(words)} if words else set()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


@dataclass(frozen=True)
class ScriptFingerprint:
    text_shingles: frozenset[tuple[str, ...]]
    frame_shingles: frozenset[tuple[str, ...]]
    hook: tuple[str, ...]  # first-beat normalized words (the hook structure)
    beat_kinds: tuple[str, ...]  # scene grammar order


def fingerprint_script(beats: list[str], scene_kinds: list[str]) -> ScriptFingerprint:
    all_text = " ".join(beats)
    hook = tuple(_normalize(beats[0])[:12]) if beats else ()
    return ScriptFingerprint(
        frozenset(shingles(all_text)),
        frozenset(structural_shingles(all_text)),
        hook,
        tuple(scene_kinds),
    )


@dataclass(frozen=True)
class Comparison:
    against: str  # identifier of the prior piece
    phrase_overlap: float
    frame_overlap: float
    hook_overlap: float
    beat_order_match: float
    image_distance: int | None = None


@dataclass(frozen=True)
class Thresholds:
    too_similar_phrase: float = 0.50
    needs_diff_phrase: float = 0.30
    hook_match: float = 0.75
    beat_match: float = 0.9
    image_hamming_max: int = 8
    # Companion channels tighten these (14.2): fifty near-identical "good morning" clips is the
    # failure mode. mass_production_min_matches near-identical siblings trips the risk verdict.
    mass_production_min_matches: int = 3


@dataclass(frozen=True)
class OriginalityDecision:
    verdict: OriginalityVerdict
    explanations: tuple[str, ...]
    comparisons: tuple[Comparison, ...] = field(default_factory=tuple)

    @property
    def blocking(self) -> bool:
        return self.verdict in BLOCKING


def compare(
    new: ScriptFingerprint,
    prior: ScriptFingerprint,
    against: str,
    *,
    new_image: Image.Image | None = None,
    prior_image: Image.Image | None = None,
) -> Comparison:
    hook_overlap = jaccard(set(new.hook), set(prior.hook)) if new.hook and prior.hook else 0.0
    n = min(len(new.beat_kinds), len(prior.beat_kinds))
    beat_match = (
        (sum(1 for a, b in zip(new.beat_kinds, prior.beat_kinds, strict=False) if a == b) / n)
        if n
        else 0.0
    )
    image_distance = None
    if new_image is not None and prior_image is not None:
        image_distance = hamming(dhash(new_image), dhash(prior_image))
    return Comparison(
        against=against,
        phrase_overlap=jaccard(set(new.text_shingles), set(prior.text_shingles)),
        frame_overlap=jaccard(set(new.frame_shingles), set(prior.frame_shingles)),
        hook_overlap=hook_overlap,
        beat_order_match=beat_match,
        image_distance=image_distance,
    )


def decide(
    comparisons: list[Comparison],
    *,
    thresholds: Thresholds = Thresholds(),
    declared_adaptation_of: str | None = None,
) -> OriginalityDecision:
    explanations: list[str] = []
    near_identical = 0
    worst: OriginalityVerdict = OriginalityVerdict.original
    order = [
        OriginalityVerdict.original,
        OriginalityVerdict.acceptable_adaptation,
        OriginalityVerdict.needs_differentiation,
        OriginalityVerdict.too_similar,
        OriginalityVerdict.mass_production_risk,
    ]

    def bump(v: OriginalityVerdict) -> None:
        nonlocal worst
        if order.index(v) > order.index(worst):
            worst = v

    for c in comparisons:
        is_declared = declared_adaptation_of == c.against
        noun_swap = (
            c.frame_overlap >= 0.75 and c.phrase_overlap >= 0.15
        )  # same frame, swapped content
        near_image = (
            c.image_distance is not None and c.image_distance <= thresholds.image_hamming_max
        )
        if (
            c.phrase_overlap >= thresholds.too_similar_phrase
            or noun_swap
            or (near_image and c.phrase_overlap >= 0.10)
        ):
            near_identical += 1
            if is_declared:
                bump(OriginalityVerdict.acceptable_adaptation)
                explanations.append(
                    f"{c.against}: {c.phrase_overlap:.0%} phrase overlap, but it is a declared adaptation of this piece"
                )
            else:
                bump(OriginalityVerdict.too_similar)
                detail = f"{c.phrase_overlap:.0%} of 3-word phrases match"
                if noun_swap and c.phrase_overlap < thresholds.too_similar_phrase:
                    detail = f"sentence frame is {c.frame_overlap:.0%} identical with only content words swapped"
                if near_image:
                    detail += f"; the visual fingerprint is {c.image_distance} bits from an existing design"
                explanations.append(f"{c.against}: {detail}")
            continue
        if near_image:
            bump(OriginalityVerdict.needs_differentiation)
            explanations.append(
                f"{c.against}: near-identical visual design ({c.image_distance} bits) — vary the layout"
            )
            continue
        if c.hook_overlap >= thresholds.hook_match and c.beat_order_match >= thresholds.beat_match:
            bump(OriginalityVerdict.needs_differentiation)
            explanations.append(
                f"{c.against}: same hook structure ({c.hook_overlap:.0%}) and identical beat order — vary the opening or restructure"
            )
        elif c.phrase_overlap >= thresholds.needs_diff_phrase:
            bump(OriginalityVerdict.needs_differentiation)
            explanations.append(
                f"{c.against}: {c.phrase_overlap:.0%} phrase overlap sits above the differentiation threshold"
            )
    if near_identical >= thresholds.mass_production_min_matches:
        bump(OriginalityVerdict.mass_production_risk)
        explanations.append(
            f"{near_identical} near-identical prior pieces — mass-production pattern across the channel's own history"
        )
    if not explanations:
        explanations.append(
            "no prior piece shares phrases, hook structure, or visual fingerprint beyond thresholds"
        )
    return OriginalityDecision(worst, tuple(explanations), tuple(comparisons))
