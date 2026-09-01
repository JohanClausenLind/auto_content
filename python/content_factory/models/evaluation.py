"""Evaluation harness (18.9): versioned packs per capability; approval is per model x skill.
Deterministic scorers only; escalation is never based on a model's self-reported confidence."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from pydantic import Field

from content_factory.schemas.base import SchemaModel, SemVer
from content_factory.schemas.skills import ModelDescriptor, QualityFloor


class EvalCase(SchemaModel):
    case_id: str = Field(min_length=1)
    input: dict[str, str]
    expected: dict[str, str | float]


class EvaluationPack(SchemaModel):
    pack_id: str = Field(min_length=1)
    version: SemVer
    skill_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    scorer: Literal["json_field_exact", "numeric_within_tolerance"]
    tolerance: float = Field(default=0.0, ge=0)
    cases: tuple[EvalCase, ...] = Field(min_length=1)


class ModelEvaluationResult(SchemaModel):
    pack_id: str
    pack_version: SemVer
    skill_id: str
    alias: str
    metric: str
    value: float = Field(ge=0, le=1)
    cases_total: int
    cases_passed: int
    passed_floor: bool
    floor: float


ModelCallable = Callable[[dict[str, str]], str]
"""The system under test: pack-case input → raw model output (already routed/executed)."""


def _score(pack: EvaluationPack, raw: str, expected: dict[str, str | float]) -> bool:
    try:
        data = json.loads(raw)
    except ValueError:
        return False
    if pack.scorer == "json_field_exact":
        return all(str(data.get(k)) == str(v) for k, v in expected.items())
    for k, v in expected.items():
        got = data.get(k)
        if not isinstance(got, int | float) or not isinstance(v, int | float):
            return False
        if abs(got - v) > pack.tolerance:
            return False
    return True


def run_pack(
    pack: EvaluationPack, model: ModelCallable, *, alias: str, floor: QualityFloor
) -> ModelEvaluationResult:
    passed = 0
    for case in pack.cases:
        try:
            raw = model(case.input)
        except Exception:  # noqa: S112 - a crashing candidate scores zero, never escalates itself
            continue
        if _score(pack, raw, case.expected):
            passed += 1
    value = passed / len(pack.cases)
    ok = value >= floor.minimum if floor.higher_is_better else value <= floor.minimum
    return ModelEvaluationResult(
        pack_id=pack.pack_id,
        pack_version=pack.version,
        skill_id=pack.skill_id,
        alias=alias,
        metric=pack.metric,
        value=round(value, 4),
        cases_total=len(pack.cases),
        cases_passed=passed,
        passed_floor=ok,
        floor=floor.minimum,
    )


def apply_approval(
    catalog: list[ModelDescriptor], result: ModelEvaluationResult
) -> list[ModelDescriptor]:
    """Approval expires on material change by construction: it is granted per exact evaluation."""
    out: list[ModelDescriptor] = []
    for m in catalog:
        if m.alias != result.alias:
            out.append(m)
            continue
        approved = set(m.approved_for_skills)
        if result.passed_floor:
            approved.add(result.skill_id)
        else:
            approved.discard(result.skill_id)
        out.append(m.model_copy(update={"approved_for_skills": tuple(sorted(approved))}))
    return out
