"""Revision Box critique mapping (16.5) — deterministic fixture mapper.

The production skill ``critique.map_feedback`` uses a model role behind the same interface; this
module is the deterministic policy layer that (a) provides the offline/fixture implementation and
(b) enforces the non-negotiables no model may override: policy-violating requests are refused,
fact/rights/disclosure/publish-scope changes surface a gate, ambiguity asks one question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from content_factory.schemas.editing import (
    OPERATION_INVALIDATION,
    ChangeSceneVariant,
    ClarifyingQuestion,
    CritiqueCategory,
    CritiqueFinding,
    DependencyImpact,
    EditOperation,
    FixPlan,
    GateRequired,
    InvalidationScope,
    Refusal,
    ReplaceTextRange,
    RetimeBeat,
    RevisionOutcome,
    Severity,
    UpdateChartEncoding,
)


class UnitKind(StrEnum):
    chart_scene = "chart_scene"
    scene = "scene"
    text = "text"
    carousel_card = "carousel_card"
    article_section = "article_section"


@dataclass(frozen=True)
class ArtifactUnit:
    """What the mapper knows about one addressable unit of the bound artifact revision."""

    unit_id: str
    kind: UnitKind
    label: str
    ordinal: int
    duration_frames: int | None = None
    claim_linked: bool = False


@dataclass(frozen=True)
class ArtifactContext:
    artifact_id: str
    revision_hash: str
    units: tuple[ArtifactUnit, ...]

    def of_kind(self, *kinds: UnitKind) -> list[ArtifactUnit]:
        return [u for u in self.units if u.kind in kinds]


# --- policy: requests that are refused outright -------------------------------------------------
_REFUSALS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(
            r"\b(remove|delete|drop|hide|strip)\b.*\b(source|citation|reference|attribution)s?\b",
            re.I,
        ),
        "citations_required",
        "Source citations are required by the research policy and cannot be removed from an output.",  # noqa: E501
    ),
    (
        re.compile(
            r"\b(remove|delete|hide|drop)\b.*\b(disclosure|ai label|sponsored label|ad label)\b",
            re.I,
        ),
        "disclosure_required",
        "Required disclosures are mapped deterministically from platform policy and cannot be removed.",  # noqa: E501
    ),
    (
        re.compile(r"\b(remove|strip|erase)\b.*\bwatermark\b", re.I),
        "rights",
        "Removing watermarks from media is a rights violation; the request is refused.",
    ),
)

# --- gates: requests that need a different workflow than a visual FixPlan ------------------------
_GATES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(
            r"\b(change|update|fix|correct|make)\b.*\b(number|figure|statistic|percent|value|date|year)\b",
            re.I,
        ),
        "evidence",
        "Changing a sourced figure reopens evidence validation; the number must resolve to captured evidence.",  # noqa: E501
    ),
    (
        re.compile(
            r"\b(post|publish|send)\b.*\b(to|on)\b.*\b(youtube|tiktok|instagram|x|twitter|bluesky|mastodon|linkedin|everywhere|all accounts)\b",  # noqa: E501
            re.I,
        ),
        "publish_scope",
        "Adding or changing destinations is a publish-scope change that requires explicit selection.",  # noqa: E501
    ),
    (
        re.compile(
            r"\b(use|add|insert)\b.*\b(their|that|this) (photo|image|footage|clip|logo)\b", re.I
        ),
        "rights",
        "Using third-party media requires a rights record before it can be placed.",
    ),
)

_MOBILE_LEGIBILITY = re.compile(
    r"\b(unreadable|illegible|too small|can'?t read|hard to read)\b", re.I
)
_CHART_WORDS = re.compile(r"\b(chart|graph|plot|axis|bars?|labels?)\b", re.I)
_PACING_SLOW = re.compile(
    r"\b(too slow|drags|get to the point|faster|tighten|shorter intro|intro too long)\b", re.I
)
_INTRO = re.compile(r"\b(intro|opening|start|beginning|hook)\b", re.I)
_CARD_REF = re.compile(
    r"\b(first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th)\b.*\bcard\b|\bcard\s*(\d+)\b", re.I
)
_ORDINALS = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
}
_PRESS_RELEASE = re.compile(
    r"\b(press release|corporate|salesy|marketing speak|too formal)\b", re.I
)
_CARD_SAY = re.compile(
    r"\bcard\s*(\d+)\b[^\"']*(?:should\s+(?:say|read)|change\s+to|make\s+it\s+say)\s*"
    r"[\"'](?P<text>[^\"']{3,300})[\"']",
    re.I,
)


def map_feedback(feedback: str, ctx: ArtifactContext) -> RevisionOutcome:
    text = feedback.strip()
    for pattern, policy, reason in _REFUSALS:
        if pattern.search(text):
            return Refusal(policy=policy, reason=reason)
    for pattern, gate, reason in _GATES:
        if pattern.search(text):
            return GateRequired(gate=gate, reason=reason)  # type: ignore[arg-type]

    if _MOBILE_LEGIBILITY.search(text) and _CHART_WORDS.search(text):
        charts = ctx.of_kind(UnitKind.chart_scene)
        if len(charts) == 1:
            unit = charts[0]
            return _plan(
                CritiqueFinding(
                    category=CritiqueCategory.legibility,
                    severity=Severity.major,
                    target_unit_ids=(unit.unit_id,),
                    summary="Chart labels are too small for mobile viewing.",
                    evidence=text[:200],
                ),
                (
                    UpdateChartEncoding(
                        scene_id=unit.unit_id,
                        encoding_patch={
                            "label_size": "large",
                            "max_categories": 6,
                            "legend": "inline",
                        },
                    ),
                ),
                units=(unit.unit_id,),
                plain=f"Enlarge labels, cap categories at six, and move the legend inline on {unit.label}. Data is unchanged.",  # noqa: E501
                seconds=25,
            )
        if len(charts) > 1:
            return ClarifyingQuestion(
                question="Which chart do you mean — " + " or ".join(u.label for u in charts) + "?",
                candidate_unit_ids=tuple(u.unit_id for u in charts),
            )

    if _PACING_SLOW.search(text) and _INTRO.search(text):
        scenes = sorted(ctx.of_kind(UnitKind.scene, UnitKind.chart_scene), key=lambda u: u.ordinal)
        intro = next((u for u in scenes if u.ordinal == 0 and u.duration_frames), None)
        if intro and intro.duration_frames:
            new_len = max(30, int(intro.duration_frames * 0.6))
            return _plan(
                CritiqueFinding(
                    category=CritiqueCategory.pacing,
                    severity=Severity.major,
                    target_unit_ids=(intro.unit_id,),
                    summary="The opening beat runs long before the first point lands.",
                    evidence=text[:200],
                ),
                (RetimeBeat(scene_id=intro.unit_id, duration_frames=new_len),),
                units=(intro.unit_id,),
                plain=f"Shorten {intro.label} from {intro.duration_frames} to {new_len} frames; narration and captions recompile from measured audio.",  # noqa: E501
                seconds=40,
            )

    say = _CARD_SAY.search(text)
    if say:
        cards = sorted(ctx.of_kind(UnitKind.carousel_card), key=lambda u: u.ordinal)
        idx = int(say.group(1)) - 1
        if 0 <= idx < len(cards):
            target = cards[idx]
            return _plan(
                CritiqueFinding(
                    category=CritiqueCategory.tone,
                    severity=Severity.minor,
                    target_unit_ids=(target.unit_id,),
                    summary=f"Operator rewrote the text of {target.label}.",
                ),
                (
                    ReplaceTextRange(
                        unit_id=target.unit_id,
                        start=0,
                        end=0,
                        replacement=say.group("text"),
                        expected_before=None,
                    ),
                ),
                units=(target.unit_id,),
                plain=f"Replace the text of {target.label} with the wording you gave; only that card re-renders.",  # noqa: E501
                seconds=20,
            )
        return ClarifyingQuestion(
            question=f"There are {len(cards)} cards; which one do you mean?",
            candidate_unit_ids=tuple(u.unit_id for u in cards),
        )

    m = _CARD_REF.search(text)
    if m and re.search(r"\bmatch\b", text, re.I):
        cards = sorted(ctx.of_kind(UnitKind.carousel_card), key=lambda u: u.ordinal)
        target_ordinal = _ORDINALS.get(m.group(1).lower(), None) if m.group(1) else int(m.group(2))
        target = next((c for c in cards if c.ordinal == (target_ordinal or 0) - 1), None)
        if target:
            return _plan(
                CritiqueFinding(
                    category=CritiqueCategory.consistency,
                    severity=Severity.minor,
                    target_unit_ids=(target.unit_id,),
                    summary="One card uses a different variant than its siblings.",
                ),
                (ChangeSceneVariant(scene_id=target.unit_id, variant="sibling_default"),),
                units=(target.unit_id,),
                plain=f"Switch {target.label} to the same layout variant as the other cards.",
                seconds=15,
            )

    if _PRESS_RELEASE.search(text):
        texts = ctx.of_kind(UnitKind.text, UnitKind.article_section)
        if len(texts) == 1:
            unit = texts[0]
            if unit.claim_linked:
                return GateRequired(
                    gate="evidence",
                    reason="This passage carries sourced claims; a tone rewrite must preserve them, so it runs through claim-aware rewrite with evidence re-validation.",  # noqa: E501
                )
            return _plan(
                CritiqueFinding(
                    category=CritiqueCategory.tone,
                    severity=Severity.minor,
                    target_unit_ids=(unit.unit_id,),
                    summary="Register reads as promotional rather than editorial.",
                ),
                (ReplaceTextRange(unit_id=unit.unit_id, start=0, end=0, replacement=""),),
                units=(unit.unit_id,),
                plain=f"Rewrite {unit.label} in a plainer editorial register (claim-free passage).",
                seconds=30,
            )
        if len(texts) > 1:
            return ClarifyingQuestion(
                question="Which passage sounds off — " + ", ".join(u.label for u in texts) + "?",
                candidate_unit_ids=tuple(u.unit_id for u in texts),
            )

    return ClarifyingQuestion(
        question="I couldn't map that to a specific part of this output. Which element bothers you, and what should change?",  # noqa: E501
        candidate_unit_ids=tuple(u.unit_id for u in ctx.units[:6]),
    )


def _invalidation_for(ops: tuple[EditOperation, ...]) -> tuple[InvalidationScope, ...]:
    """Scopes an edit invalidates, read off the canonical OPERATION_INVALIDATION table.

    First-seen order is preserved so the tuple stays deterministic across ops.
    """
    scopes: list[InvalidationScope] = []
    for op in ops:
        for scope in OPERATION_INVALIDATION[op.op]:
            if scope not in scopes:
                scopes.append(scope)
    return tuple(scopes)


def _plan(
    finding: CritiqueFinding,
    ops: tuple[EditOperation, ...],
    *,
    units: tuple[str, ...],
    plain: str,
    seconds: int,
    cost_usd: float = 0.0,
) -> FixPlan:
    return FixPlan(
        findings=(finding,),
        operations=ops,
        impact=DependencyImpact(affected_unit_ids=units, invalidates=_invalidation_for(ops)),
        plain_language=plain,
        estimated_cost_usd=cost_usd,
        estimated_seconds=seconds,
    )
