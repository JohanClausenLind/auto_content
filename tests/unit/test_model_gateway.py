"""Gateway policy semantics: local_only zero cloud calls; schema retry; budget settle; egress-less
skills never see cloud candidates; pause raises with the full ExecutionDecision."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from content_factory.budgets.ledger import Cap, CostLedger, Scope
from content_factory.hardware.probe import mock_inventory
from content_factory.models.gateway import (
    EndpointConfig,
    ExecutionPausedError,
    GatewayError,
    ModelGateway,
    SchemaRetryExhaustedError,
)
from content_factory.schemas.skills import (
    PRESETS,
    CostEstimator,
    ExecutionLocation,
    ExecutorType,
    Lifecycle,
    ModelDescriptor,
    QualityFloor,
    SkillManifest,
    SkillPermissions,
)

CATALOG = [
    ModelDescriptor(
        alias="fast_structured",
        provider="ollama",
        model_id="qwen3:8b",
        license="Apache-2.0",
        commercial_use=True,
        location=ExecutionLocation.local_gpu,
        vram_bytes_estimate=6 * 1024**3,
        approved_for_skills=("fixture.echo",),
    ),
    ModelDescriptor(
        alias="fast_structured_cloud",
        provider="anthropic",
        model_id="claude-haiku-4-5-20251001",
        license="commercial",
        commercial_use=True,
        location=ExecutionLocation.cloud,
        usd_per_million_input_tokens=1.0,
        usd_per_million_output_tokens=5.0,
        approved_for_skills=("fixture.echo",),
    ),
]
ENDPOINTS = {
    "ollama": EndpointConfig(
        provider="ollama",
        litellm_prefix="ollama",
        api_base="http://127.0.0.1:11434",
        is_cloud=False,
    ),
    "anthropic": EndpointConfig(
        provider="anthropic",
        litellm_prefix="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        is_cloud=True,
    ),
}


class Outline(BaseModel):
    title: str
    sections: list[str]


def skill(**over: Any) -> SkillManifest:
    base: dict[str, Any] = dict(
        skill_id="fixture.echo",
        version="1.0.0",
        status=Lifecycle.active,
        purpose="echo",
        input_schema="EditBatch",
        output_schema="EditBatch",
        executor=ExecutorType.model_role,
        implementation_ref="x:y",
        permitted_locations=(ExecutionLocation.local_gpu, ExecutionLocation.cloud),
        required_models=("fast_structured", "fast_structured_cloud"),
        permissions=SkillPermissions(network_egress=True),
        quality_floor=QualityFloor(evaluation_pack="p", metric="m", minimum=0.9),
        license_evidence="Apache-2.0",
        cost=CostEstimator(kind="per_token", usd=0),
        timeout_seconds=60,
        max_retries=1,
    )
    base.update(over)
    return SkillManifest(**base)


def gateway(calls: list[dict], reply: str = "", inventory: str = "rtx3090") -> ModelGateway:
    led = CostLedger()
    led.set_cap(Cap(scope=Scope.monthly, key="m", limit_usd=10.0))

    def fake_completion(**kwargs: Any) -> dict:
        calls.append(kwargs)
        text = reply or json.dumps({"title": "T", "sections": ["a", "b"]})
        return {
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 500, "completion_tokens": 100},
        }

    return ModelGateway(
        catalog=CATALOG,
        endpoints=ENDPOINTS,
        ledger=led,
        inventory=mock_inventory(inventory),
        completion_fn=fake_completion,
    )


def run(gw: ModelGateway, policy_name: str, s: SkillManifest | None = None):
    return gw.complete_structured(
        s or skill(),
        PRESETS[policy_name],
        Outline,
        [{"role": "user", "content": "outline wind story"}],
        budget_scopes=[(Scope.monthly, "m")],
    )


def test_local_only_calls_only_the_local_endpoint() -> None:
    calls: list[dict] = []
    result = run(gateway(calls), "offline")
    assert result.model_alias == "fast_structured"
    assert len(calls) == 1
    assert calls[0]["model"] == "ollama/qwen3:8b"
    assert calls[0]["api_base"] == "http://127.0.0.1:11434"
    assert "api_key" not in calls[0]
    assert result.actual_usd == 0.0


def test_local_only_with_no_local_candidate_pauses_and_makes_zero_calls() -> None:
    calls: list[dict] = []
    gw = gateway(calls, inventory="cpu_only")
    with pytest.raises(ExecutionPausedError) as exc:
        run(gw, "offline")
    assert calls == []  # provable zero egress: the executor was never invoked
    assert exc.value.decision.outcome in {"pause_insufficient_quality", "pause_no_candidate"}
    assert any(r.alias == "fast_structured_cloud" for r in exc.value.decision.rejected)


def test_egressless_skill_never_sees_cloud_even_on_cpu_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    gw = gateway(calls, inventory="cpu_only")
    s = skill(permissions=SkillPermissions(network_egress=False))
    with pytest.raises(ExecutionPausedError):
        gw.complete_structured(
            s,
            PRESETS["balanced"],
            Outline,
            [{"role": "user", "content": "x"}],
            budget_scopes=[(Scope.monthly, "m")],
        )
    assert calls == []


def test_cloud_call_settles_budget_and_requires_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    gw = gateway(calls, inventory="cpu_only")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(GatewayError, match="ANTHROPIC_API_KEY"):
        run(gw, "balanced")
    assert gw.ledger.headroom(Scope.monthly, "m") == 10.0  # reservation released on failure
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    result = run(gw, "balanced")
    assert result.model_alias == "fast_structured_cloud"
    assert calls[-1]["model"] == "anthropic/claude-haiku-4-5-20251001"
    assert calls[-1]["api_key"] == "test-key"
    assert result.actual_usd == pytest.approx(500 / 1e6 * 1.0 + 100 / 1e6 * 5.0)
    assert gw.ledger.spent(Scope.monthly, "m") == result.actual_usd
    assert gw.ledger.events[-1].provider == "anthropic"


def test_schema_retry_then_exhaustion() -> None:
    calls: list[dict] = []
    replies = iter(["not json at all", '```json\n{"title": "T", "sections": ["a"]}\n```'])

    def fake_completion(**kwargs: Any) -> dict:
        calls.append(kwargs)
        return {
            "choices": [{"message": {"content": next(replies)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    led = CostLedger()
    led.set_cap(Cap(scope=Scope.monthly, key="m", limit_usd=10.0))
    gw = ModelGateway(
        catalog=CATALOG,
        endpoints=ENDPOINTS,
        ledger=led,
        inventory=mock_inventory("rtx3090"),
        completion_fn=fake_completion,
    )
    result = run(gw, "offline")
    assert result.attempts == 2
    assert isinstance(result.value, Outline) and result.value.sections == ["a"]

    bad = gateway(calls := [], reply="still not json")
    with pytest.raises(SchemaRetryExhaustedError):
        run(bad, "offline")
    assert bad.ledger.headroom(Scope.monthly, "m") == 10.0
