"""Topic ranking and title-promise checks: what to make next, and what its title may promise."""

from __future__ import annotations

import difflib
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations
from types import MappingProxyType
from typing import Annotated

from pydantic import Field

from content_factory.explainer.errors import ContractIssue
from content_factory.schemas.base import OpaqueId, SchemaModel
from content_factory.schemas.explainer import (
    Calculation,
    Claim,
    ClaimOperand,
    EvidencePack,
    ScriptPlan,
)

Score = Annotated[int, Field(ge=1, le=5)]
SourceUrl = Annotated[str, Field(min_length=8, max_length=2000, pattern=r"^https?://\S+$")]
CRITERIA = (
    "audience_question",
    "evidence_availability",
    "original_angle",
    "visual_explainability",
    "shelf_life",
    "production_effort",
)
# Equal until the creator ranks them: CHANNEL_PROFILE.md orders the criteria but sets no weights.
DEFAULT_WEIGHTS: Mapping[str, float] = MappingProxyType(dict.fromkeys(CRITERIA, 1.0))
SIMILAR_TITLE_RATIO = 0.8


class TopicCandidate(SchemaModel):
    """A topic the editor might produce, scored 1-5 on each of the channel's ranking criteria."""

    topic_id: OpaqueId
    question: str = Field(min_length=1, max_length=300)
    angle: str = Field(min_length=1, max_length=500)
    audience_question: Score
    evidence_availability: Score
    original_angle: Score
    visual_explainability: Score
    shelf_life: Score
    production_effort: Score  # 5 = least effort, so every criterion reads higher-is-better
    sponsor_fit: Score  # secondary: breaks ties, never adds to the score
    sources: tuple[SourceUrl, ...] = ()
    notes: str = Field(default="", max_length=1000)


@dataclass(frozen=True)
class RankedTopic:
    """A candidate's place; tied means its score equals another's and sponsor fit set the order."""

    rank: int
    candidate: TopicCandidate
    score: float
    tied: bool


def rank_topics(
    candidates: Sequence[TopicCandidate], weights: Mapping[str, float] = DEFAULT_WEIGHTS
) -> list[RankedTopic]:
    """Best first by the weighted sum of the six primary criteria; sponsor fit only breaks ties."""
    unknown = sorted(set(weights) - set(CRITERIA))
    missing = [c for c in CRITERIA if c not in weights]
    if unknown or missing:
        msg = (
            f"weights must name exactly {', '.join(CRITERIA)}; unknown {unknown}, missing {missing}"
        )
        raise ValueError(msg)
    negative = [k for k in CRITERIA if weights[k] < 0]
    if negative:
        msg = f"weights must not be negative: {', '.join(negative)}"
        raise ValueError(msg)
    duplicates = sorted(k for k, n in Counter(c.topic_id for c in candidates).items() if n > 1)
    if duplicates:
        msg = f"duplicate topic ids: {', '.join(duplicates)}"
        raise ValueError(msg)
    # Rounded so float noise in fractional weights never splits a genuine tie.
    scored = [(round(sum(weights[k] * getattr(c, k) for k in CRITERIA), 9), c) for c in candidates]
    scored.sort(key=lambda pair: (-pair[0], -pair[1].sponsor_fit, pair[1].topic_id))
    counts = Counter(score for score, _ in scored)
    return [
        RankedTopic(rank=i, candidate=c, score=score, tied=counts[score] > 1)
        for i, (score, c) in enumerate(scored, 1)
    ]


def check_promises(script: ScriptPlan, pack: EvidencePack) -> list[ContractIssue]:
    """Each promise cites real claims, rests on at least one observation, and stands apart."""
    claims = {c.claim_id: c for c in pack.claims}
    calcs = {c.calc_id: c for c in pack.calculations}
    issues: list[ContractIssue] = []
    for i, promise in enumerate(script.promises):
        where = f"ScriptPlan.promises[{i}].claim_ids"
        for cid in promise.claim_ids:
            if cid not in claims:
                message = f"promise {promise.title!r} cites unknown claim {cid}."
                fix = "cite a claim in the frozen pack."
                issues.append(ContractIssue("invalid_reference", where, message, fix, (cid,)))
        cited = [claims[c] for c in promise.claim_ids if c in claims]
        if not cited or any(_observed(c, claims, calcs, frozenset()) for c in cited):
            continue
        to_source = dict.fromkeys(
            leaf for c in cited for leaf in _unobserved_leaves(c, claims, calcs, frozenset())
        )
        classes = ", ".join(f"{c.claim_id} ({c.epistemic_class})" for c in cited)
        message = f"promise {promise.title!r} rests on no observation: {classes}."
        fix = (
            f"source {', '.join(to_source)} from observed evidence or cite an observed claim; "
            "an illustrative assumption never carries a title."
        )
        ids = tuple(c.claim_id for c in cited)
        issues.append(ContractIssue("evidence", where, message, fix, ids))
    return issues + _similar_titles(script)


def ranking_markdown(ranked: Sequence[RankedTopic]) -> str:
    """The ranking as a table, then each topic's angle, sources and notes, for the editor."""
    lines = [
        "# Topic ranking",
        "",
        "Score is the weighted sum of the six primary criteria, each 1-5 (effort 5 = least "
        "effort). Sponsor fit only orders topics whose scores tie, marked `=`.",
        "",
        "| Rank | Topic | Question | Score | Audience | Evidence | Angle | Visual | Shelf life "
        "| Effort | Sponsor |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in ranked:
        c = r.candidate
        cells = [
            f"{r.rank}=" if r.tied else str(r.rank),
            f"`{c.topic_id}`",
            _cell(c.question),
            f"{r.score:g}",
            *(str(getattr(c, k)) for k in CRITERIA),
            str(c.sponsor_fit),
        ]
        lines.append(f"| {' | '.join(cells)} |")
    for r in ranked:
        c = r.candidate
        lines += ["", f"## {r.rank}. {c.question}", "", c.angle]
        if c.sources:
            lines += ["", "Sources:", *(f"- <{url}>" for url in c.sources)]
        if c.notes:
            lines += ["", f"Notes: {c.notes}"]
    return "\n".join(lines) + "\n"


def _observed(
    claim: Claim,
    claims: Mapping[str, Claim],
    calcs: Mapping[str, Calculation],
    seen: frozenset[str],
) -> bool:
    """An observation, or a non-illustrative derived claim whose every input traces to one."""
    if claim.epistemic_class == "observation":
        return True
    inputs = _inputs(claim, calcs)
    if claim.epistemic_class == "illustrative_assumption" or not inputs:
        return False
    below = seen | {claim.claim_id}
    return all(
        cid in claims and cid not in below and _observed(claims[cid], claims, calcs, below)
        for cid in inputs
    )


def _unobserved_leaves(
    claim: Claim,
    claims: Mapping[str, Claim],
    calcs: Mapping[str, Calculation],
    seen: frozenset[str],
) -> list[str]:
    """The claims to source so this one traces to observations: its unobserved base inputs."""
    if _observed(claim, claims, calcs, seen):
        return []
    below = seen | {claim.claim_id}
    leaves = [
        leaf
        for cid in _inputs(claim, calcs)
        if cid in claims and cid not in below
        for leaf in _unobserved_leaves(claims[cid], claims, calcs, below)
    ]
    return leaves or [claim.claim_id]


def _inputs(claim: Claim, calcs: Mapping[str, Calculation]) -> list[str]:
    calc = calcs.get(claim.calculation_id or "")
    if calc is None:
        return []
    return [op.claim_id for op in calc.operands if isinstance(op, ClaimOperand)]


def _similar_titles(script: ScriptPlan) -> list[ContractIssue]:
    issues: list[ContractIssue] = []
    titles = [_normalised(p.title) for p in script.promises]
    for (i, a), (j, b) in combinations(enumerate(titles), 2):
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        if ratio < SIMILAR_TITLE_RATIO:
            continue
        first, second = script.promises[i].title, script.promises[j].title
        message = (
            f"title {second!r} is {ratio:.2f} similar to promise {i}'s {first!r}; "
            "the promises must be distinct."
        )
        fix = "rewrite it around a different claim or angle."
        issues.append(
            ContractIssue("invalid_value", f"ScriptPlan.promises[{j}].title", message, fix)
        )
    return issues


def _normalised(title: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", title.casefold()).split())


def _cell(text: str) -> str:
    return text.replace("|", r"\|").replace("\n", " ")
