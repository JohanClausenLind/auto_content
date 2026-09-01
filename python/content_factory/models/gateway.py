"""ModelGateway (18.2, 18.3): logical aliases in, validated structured output out.

Selection is `routing.decide` (policy, quality floors, hardware, budget); execution goes through
LiteLLM (Anthropic / OpenAI-compatible / Ollama, bring-your-own credentials) with Instructor-style
schema-validated retries. `local_only` provably makes zero cloud calls: the executor is only ever
invoked with the chosen candidate, and cloud candidates never survive the policy filter.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from content_factory.budgets.ledger import CostLedger, Scope
from content_factory.hardware.probe import mock_inventory, probe_hardware
from content_factory.models.routing import decide
from content_factory.schemas.hardware import HardwareInventory
from content_factory.schemas.skills import (
    ExecutionDecision,
    ExecutionLocation,
    ExecutionPolicy,
    ModelDescriptor,
    SkillManifest,
)

T = TypeVar("T", bound=BaseModel)


class GatewayError(Exception):
    pass


class ExecutionPausedError(GatewayError):
    """The router paused instead of dispatching (quality floor, budget, no candidate)."""

    def __init__(self, decision: ExecutionDecision) -> None:
        super().__init__(decision.explanation)
        self.decision = decision


class SchemaRetryExhaustedError(GatewayError):
    pass


@dataclass(frozen=True)
class EndpointConfig:
    """How to reach one provider (BYO). Credentials resolve from env at call time only."""

    provider: str  # matches ModelDescriptor.provider
    litellm_prefix: str  # "ollama" | "openai" | "anthropic"
    api_base: str | None = None
    api_key_env: str | None = None
    is_cloud: bool = False


@dataclass(frozen=True)
class GatewayResult:
    value: BaseModel
    decision: ExecutionDecision
    model_alias: str
    attempts: int
    input_tokens: int
    output_tokens: int
    actual_usd: float
    elapsed_s: float


CompletionFn = Callable[..., Any]


def _default_completion(**kwargs: Any) -> Any:
    import litellm

    litellm.drop_params = True
    return litellm.completion(**kwargs)


@dataclass
class ModelGateway:
    catalog: list[ModelDescriptor]
    endpoints: dict[str, EndpointConfig]
    ledger: CostLedger
    inventory: HardwareInventory | None = None
    completion_fn: CompletionFn | None = None  # injectable for tests; None = litellm
    max_schema_retries: int = 2

    def _hw(self) -> HardwareInventory:
        if self.inventory is not None:
            return self.inventory
        try:
            return probe_hardware()
        except Exception:  # pragma: no cover - probe failure falls back to cpu-only
            return mock_inventory("cpu_only")

    def complete_structured(
        self,
        skill: SkillManifest,
        policy: ExecutionPolicy,
        response_model: type[T],
        messages: list[dict[str, str]],
        *,
        budget_scopes: list[tuple[Scope, str]],
        estimated_tokens: int = 2000,
        temperature: float = 0.0,
        seed: int | None = 7,
    ) -> GatewayResult:
        if not skill.permissions.network_egress:
            # A skill without network egress may still call loopback/tailnet model servers, but
            # never a cloud provider, whatever the policy says.
            catalog: Iterable[ModelDescriptor] = [
                m for m in self.catalog if m.location != ExecutionLocation.cloud
            ]
        else:
            catalog = self.catalog
        decision = decide(
            skill,
            policy,
            catalog,
            self._hw(),
            budget_remaining_usd=min(
                (self.ledger.headroom(s, k) for s, k in budget_scopes), default=float("inf")
            ),
            estimated_tokens=estimated_tokens,
        )
        if decision.outcome != "dispatch" or decision.chosen_alias is None:
            raise ExecutionPausedError(decision)
        model = next(m for m in self.catalog if m.alias == decision.chosen_alias)
        endpoint = self.endpoints.get(model.provider)
        if endpoint is None:
            raise GatewayError(f"no endpoint configured for provider {model.provider!r}")
        if endpoint.is_cloud and model.location != ExecutionLocation.cloud:
            raise GatewayError(
                f"endpoint {model.provider!r} is cloud but the model is declared local"
            )
        reservation = self.ledger.reserve(
            decision.estimated_usd, scopes=budget_scopes, purpose=f"{skill.skill_id}:{model.alias}"
        )
        completion = self.completion_fn or _default_completion
        schema = response_model.model_json_schema()
        sys_suffix = (
            "\nReturn ONLY a JSON object matching this JSON Schema (no prose, no code fences):\n"
            + str(schema)
        )
        convo = [*messages]
        if convo and convo[0]["role"] == "system":
            convo[0] = {"role": "system", "content": convo[0]["content"] + sys_suffix}
        else:
            convo.insert(0, {"role": "system", "content": sys_suffix})
        kwargs: dict[str, Any] = {
            "model": f"{endpoint.litellm_prefix}/{model.model_id}",
            "messages": convo,
            "temperature": temperature,
        }
        if seed is not None:
            kwargs["seed"] = seed
        if endpoint.api_base:
            kwargs["api_base"] = endpoint.api_base
        if endpoint.api_key_env:
            key = os.environ.get(endpoint.api_key_env)
            if not key:
                self.ledger.release(reservation)
                raise GatewayError(f"credential env {endpoint.api_key_env!r} is not set")
            kwargs["api_key"] = key
        started = time.monotonic()
        attempts = 0
        in_tokens = out_tokens = 0
        last_error: str = ""
        try:
            for attempt in range(1, self.max_schema_retries + 2):
                attempts = attempt
                response = completion(**kwargs)
                text = (
                    response["choices"][0]["message"]["content"]
                    if isinstance(response, dict)
                    else response.choices[0].message.content
                )
                usage = (
                    response.get("usage", {})
                    if isinstance(response, dict)
                    else getattr(response, "usage", None)
                )
                if usage:
                    in_tokens += int(
                        usage["prompt_tokens"]
                        if isinstance(usage, dict)
                        else usage.prompt_tokens or 0
                    )
                    out_tokens += int(
                        usage["completion_tokens"]
                        if isinstance(usage, dict)
                        else usage.completion_tokens or 0
                    )
                try:
                    value = response_model.model_validate_json(_strip_fences(text))
                    break
                except ValidationError as exc:
                    last_error = str(exc)[:800]
                    kwargs["messages"] = [
                        *convo,
                        {"role": "assistant", "content": text},
                        {
                            "role": "user",
                            "content": f"That did not validate: {last_error}\nReturn ONLY corrected JSON.",  # noqa: E501
                        },
                    ]
            else:
                raise SchemaRetryExhaustedError(
                    f"{skill.skill_id}: output failed schema after {attempts} attempts: {last_error}"  # noqa: E501
                )
        except Exception:
            self.ledger.release(reservation)
            raise
        actual = _cost_usd(model, in_tokens, out_tokens)
        self.ledger.settle(
            reservation,
            actual,
            provider=model.provider,
            detail=f"{skill.skill_id} {model.model_id}",
        )
        return GatewayResult(
            value=value,
            decision=decision,
            model_alias=model.alias,
            attempts=attempts,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            actual_usd=actual,
            elapsed_s=round(time.monotonic() - started, 3),
        )


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
        if t.startswith("json"):
            t = t[4:].strip()
    return t


def _cost_usd(model: ModelDescriptor, in_tokens: int, out_tokens: int) -> float:
    if model.location != ExecutionLocation.cloud:
        return 0.0
    per_in = model.usd_per_million_input_tokens or 0.0
    per_out = model.usd_per_million_output_tokens or 0.0
    return round(in_tokens / 1e6 * per_in + out_tokens / 1e6 * per_out, 6)
