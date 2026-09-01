"""Default model catalog: qwen38-ridge primary, qwen3:8b fallback, Ollama-only endpoints."""

from __future__ import annotations

from content_factory.budgets.ledger import Cap, CostLedger, Scope
from content_factory.hardware.probe import mock_inventory
from content_factory.models.catalog import build_gateway, default_catalog, default_endpoints
from content_factory.models.gateway import ModelGateway, _strip_fences
from content_factory.models.routing import decide
from content_factory.schemas.skills import (
    PRESETS,
    CostEstimator,
    ExecutionLocation,
    ExecutorType,
    Lifecycle,
    SkillManifest,
    SkillPermissions,
)


def _skill() -> SkillManifest:
    return SkillManifest(
        skill_id="ops.model_check",
        version="1.0.0",
        status=Lifecycle.active,
        purpose="smoke",
        input_schema="EditBatch",
        output_schema="EditBatch",
        executor=ExecutorType.model_role,
        implementation_ref="x:y",
        permitted_locations=(ExecutionLocation.local_gpu, ExecutionLocation.local_cpu),
        required_models=("local_structured", "local_structured_small"),
        permissions=SkillPermissions(network_egress=False),
        license_evidence="Apache-2.0",
        cost=CostEstimator(kind="per_token", usd=0),
        timeout_seconds=60,
        max_retries=1,
    )


def test_catalog_prefers_ridge_on_the_3090_and_falls_back_on_small_gpus() -> None:
    catalog = default_catalog()
    assert [m.model_id for m in catalog] == ["qwen38-ridge:latest", "qwen3:8b"]
    assert all(m.location != ExecutionLocation.cloud for m in catalog)
    big = decide(
        _skill(),
        PRESETS["private_local"],
        catalog,
        mock_inventory("rtx3090"),
        budget_remaining_usd=1.0,
    )
    assert big.outcome == "dispatch" and big.chosen_alias == "local_structured"
    small = decide(
        _skill(),
        PRESETS["private_local"],
        catalog,
        mock_inventory("rtx4060_8gb"),
        budget_remaining_usd=1.0,
    )
    assert small.outcome == "dispatch" and small.chosen_alias == "local_structured_small"


def test_build_gateway_wires_ollama_chat_endpoint_only() -> None:
    gateway = build_gateway()
    assert isinstance(gateway, ModelGateway)
    assert set(gateway.endpoints) == {"ollama"}
    endpoint = gateway.endpoints["ollama"]
    assert endpoint.litellm_prefix == "ollama_chat" and not endpoint.is_cloud
    assert endpoint.api_key_env is None  # local models need no credentials
    assert endpoint.api_base and "127.0.0.1:11434" in endpoint.api_base
    assert default_endpoints().keys() == {"ollama"}


def test_strip_fences_handles_reasoning_blocks_and_code_fences() -> None:
    assert _strip_fences('<think>hmm\nplan</think>\n{"a": 1}') == '{"a": 1}'
    assert _strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_fences('<think>x</think>\n```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _strip_fences('{"a": 1}') == '{"a": 1}'


def test_local_gateway_settles_zero_cost() -> None:
    ledger = CostLedger()
    ledger.set_cap(Cap(scope=Scope.monthly, key="ops", limit_usd=1.0))
    gateway = build_gateway(ledger=ledger)

    def fake_completion(**kwargs: object) -> dict:
        assert str(kwargs["model"]).startswith("ollama_chat/")
        return {
            "choices": [{"message": {"content": '<think>plan</think>{"answer": "hi"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    gateway.completion_fn = fake_completion
    gateway.inventory = mock_inventory("rtx3090")

    from pydantic import BaseModel

    class Reply(BaseModel):
        answer: str

    result = gateway.complete_structured(
        _skill(),
        PRESETS["private_local"],
        Reply,
        [{"role": "user", "content": "hi"}],
        budget_scopes=[(Scope.monthly, "ops")],
    )
    assert result.model_alias == "local_structured" and result.actual_usd == 0.0
    assert result.value.answer == "hi"  # type: ignore[attr-defined]
