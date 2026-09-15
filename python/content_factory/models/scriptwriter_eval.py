"""Deterministic scorers for the script writer, and the pack a build has to pass to be a default."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from content_factory.models.scriptwriter import (
    ClaimCard,
    DraftBeat,
    DraftResult,
    _dataset_values,
    _number_is_supported,
)
from content_factory.research.claims import parse_numbers
from content_factory.scenes.kinds import IMPLEMENTED_KINDS
from content_factory.schemas.documentary import EpisodeOutline
from content_factory.schemas.render import DatasetTable
from content_factory.schemas.skills import QualityFloor

BUDGET_TOLERANCE = 0.5
"""A beat may be half again over or under its section's word budget. Generous on purpose: the
budget is an arc-shaping guide, and a scorer that failed a beat for being four words long would
measure obedience rather than usefulness. What it catches is a section written at three times its
length, which is what pushes a ten-minute episode to seventeen."""


def _needs_a_spoken_line(text: str) -> bool:
    """Does this display line contain a figure a narrator would mispronounce?"""
    return bool(parse_numbers(text))


@dataclass(frozen=True)
class WriterScores:
    """One draft, six numbers, each independently actionable."""

    usable: float
    """Beats that survived validation, over beats requested. The headline number."""
    grounded: float
    """Beats whose every figure is in a dataset row or a cited claim, over beats with a figure.
    1.0 when the draft states no figures at all — a film with no numbers has invented none."""
    cited: float
    """Beats citing only claim ids that were offered, over beats that cite anything."""
    drawable: float
    """Beats whose scene kind has a renderer, over all beats."""
    arc: float
    """Outline sections that got at least one beat, over sections."""
    budget: float
    """Beats within :data:`BUDGET_TOLERANCE` of their section's word budget, over all beats."""
    spoken: float
    """Beats that supplied a `spoken_text`, over beats whose display line states a *quantity*.
    A bare year does not count: "2018" reads correctly without help. 1.0 when no line states one."""

    def as_dict(self) -> dict[str, float]:
        return {
            "usable": self.usable,
            "grounded": self.grounded,
            "cited": self.cited,
            "drawable": self.drawable,
            "arc": self.arc,
            "budget": self.budget,
            "spoken": self.spoken,
        }

    @property
    def overall(self) -> float:
        """The metric a quality floor is set against."""
        return round(min(self.as_dict().values()), 4)


def _words(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def score_draft(
    result: DraftResult,
    outline: EpisodeOutline,
    *,
    datasets: dict[str, DatasetTable] | None = None,
    cards: Sequence[ClaimCard] = (),
) -> WriterScores:
    """Score what the model returned."""
    raw = result.raw
    if raw is None or not raw.beats:
        return WriterScores(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    beats: tuple[DraftBeat, ...] = raw.beats
    total = len(beats)
    dataset_values = _dataset_values(datasets or {})
    by_id = {c.claim_id: c for c in cards}
    budgets = {s.kind: s.word_budget for s in outline.sections}

    with_numbers = 0
    grounded = 0
    with_citations = 0
    cited = 0
    drawable = 0
    in_budget = 0
    with_numerals = 0
    spoken = 0
    for beat in beats:
        figures = [
            n
            for text in (beat.display_text, beat.headline, beat.secondary, *beat.bullets)
            for n in parse_numbers(text)
        ]
        # Grounded means in a dataset row or a cited claim; citing an unoffered claim is `cited`'s
        # failure and must not be charged twice here.
        claim_values = {
            float(n.value)
            for cid in beat.claim_ids
            if cid in by_id
            for n in parse_numbers(by_id[cid].statement)
        }
        if figures:
            with_numbers += 1
            if all(_number_is_supported(n.value, dataset_values, claim_values) for n in figures):
                grounded += 1
        if beat.claim_ids:
            with_citations += 1
            if all(c in by_id for c in beat.claim_ids):
                cited += 1
        if beat.scene_kind in IMPLEMENTED_KINDS:
            drawable += 1
        budget = budgets.get(beat.section)
        if budget is None:
            in_budget += 1
        else:
            words = _words(beat.display_text)
            if abs(words - budget) <= budget * BUDGET_TOLERANCE:
                in_budget += 1
        if _needs_a_spoken_line(beat.display_text):
            with_numerals += 1
            if (beat.spoken_text or "").strip():
                spoken += 1

    covered = {b.section for b in beats}
    return WriterScores(
        usable=round(len(result.plan.beats) / total, 4) if result.plan else 0.0,
        grounded=round(grounded / with_numbers, 4) if with_numbers else 1.0,
        cited=round(cited / with_citations, 4) if with_citations else 1.0,
        drawable=round(drawable / total, 4),
        arc=round(len(covered & set(budgets)) / len(budgets), 4),
        budget=round(in_budget / total, 4),
        spoken=round(spoken / with_numerals, 4) if with_numerals else 1.0,
    )


SCRIPTWRITER_FLOOR = QualityFloor(
    evaluation_pack="scriptwriter.plan.v1",
    metric="min_axis_score",
    minimum=0.8,
    higher_is_better=True,
)
"""What a build has to clear before `execution.local_scriptwriter` may default to on.

0.8 on the *worst* axis, which is a stricter bar than it looks: it means at most one beat in five
is refused, at most one in five is off its budget, and — because `grounded` and `cited` are on the
same floor — at most one in five states a figure it cannot support. Those last two are the ones
that would put an invented number in front of a viewer, so if a build clears the others and misses
these, the answer is a better model or a better prompt, not a lower floor.
"""
