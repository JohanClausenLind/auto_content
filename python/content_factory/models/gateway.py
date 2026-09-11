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
    supports_json_schema: bool = False
    """Whether the provider can take the response schema as a *decoding constraint* rather than as
    text in the prompt.

    Verified for Ollama on 2026-09-08 against litellm 1.99.0: ``response_format={"type":
    "json_schema", "json_schema": {"schema": …}}`` is forwarded as Ollama's own top-level
    ``format``, the payload carries the raw JSON Schema, and a nested Pydantic schema with
    ``$defs``/``$ref`` comes back valid first time. Off by default, because a provider that does
    not support it and silently ignores the field would leave the model with no schema at all."""

    supports_think: bool = False
    """Whether ``think`` is a real parameter here (Ollama's per-request thinking switch). Verified
    on 2026-09-08: it reaches the payload and ``/api/chat`` returns the reasoning in its own
    ``thinking`` field, so ``content`` is clean JSON rather than prose with a ``<think>`` block in
    front of it."""


DEFAULT_NUM_CTX = 16384
"""The context window a structured call asks Ollama for, when the model allows it.

Not a tuning knob — a correctness one. Ollama uses its own default (4096) unless told otherwise,
whatever the model declares, so a request carrying a response schema plus a brief plus claim cards
was being silently truncated: the model saw a cut-off prompt and the schema was the part most
likely to fall off the end. 16384 is chosen to hold the largest structured request this repo makes
with room to spare, and it is clamped to the model's own declared window so a small model is never
asked for more than it has."""


Message = dict[str, Any]
"""One chat message. ``content`` is a string for every text call, and for a vision call it is
litellm's list of content parts (``{"type": "text"}`` / ``{"type": "image_url"}``), which the
``ollama_chat`` provider turns into Ollama's own ``images`` array. Typed loosely for exactly that
reason: the alternative was a second, near-identical entry point for the one role that sends
pictures, and the gateway's whole job is to be the single place a model call goes through."""


def _with_system_instruction(messages: list[Message], instruction: str | None) -> list[Message]:
    """Prepend the shared system instruction, unless the caller supplied its own or opted out.

    **In the gateway, not at each call site**, because a call site can forget and this one did:
    when Ollama's constrained decoding went in, the schema stopped being pasted into a system
    message and the system message went with it — so the shared rules about inventing figures and
    about house style were, for a while, sent to nobody. A rule that every role must follow cannot
    depend on every role remembering to attach it.

    A caller that has already written a system message keeps it, with the shared text prepended:
    the shared part is the floor, not a replacement. `system_instruction=""` opts out entirely,
    which is what a probe measuring the raw model wants.
    """
    from content_factory.prompting import SYSTEM_INSTRUCTION

    text = SYSTEM_INSTRUCTION if instruction is None else instruction
    if not text:
        return messages
    if messages and messages[0].get("role") == "system":
        own = messages[0].get("content", "")
        # A system message is always plain text, even on a vision call: the images ride on the
        # user turn, and prepending to a list of content parts would produce a message shape
        # neither provider accepts.
        merged = f"{text}\n\n{own}" if isinstance(own, str) else text
        return [{"role": "system", "content": merged}, *messages[1:]]
    return [{"role": "system", "content": text}, *messages]


@dataclass(frozen=True)
class GatewayOptions:
    """Everything one model call can be asked for, in one object.

    It exists because these five settings are not independent. A schema sent as a *constraint*
    needs no schema in the prompt; thinking left on eats the token budget the answer needed (a
    verified 80-token call returned an empty string with thinking on and valid JSON with it off);
    and ``num_ctx`` decides whether the prompt the caller assembled arrived at all. Passing them
    one at a time is how three of the five ended up never being passed.
    """

    structured_output: bool = True
    """Send the response schema as a decoding constraint where the provider supports one. The
    prompt still describes the task; it just stops carrying a JSON Schema as prose."""

    think: bool | None = False
    """Off by default for structured calls, and that is the measured choice: the reasoning tokens
    come out of the same budget as the answer. ``None`` leaves the model's own default alone."""

    num_ctx: int | None = None
    """None means :data:`DEFAULT_NUM_CTX`, clamped to the model's declared window."""

    max_tokens: int | None = None
    """``options.num_predict``. None leaves it to the provider."""

    keep_alive: float | str | None = None
    """How long the provider holds the weights after this call. ``0`` unloads immediately, which is
    what a lane about to load HiDream or ComfyUI onto the same card wants (see
    ``services.local.free_the_gpu``)."""

    temperature: float = 0.0
    seed: int | None = 7
    system_instruction: str | None = None
    """`None` sends `prompting.SYSTEM_INSTRUCTION`; a string replaces it; `""` sends none.

    Default `None` rather than the text itself so the shared instruction has exactly one home:
    a copy of it frozen into a dataclass default would be a second place to edit and a first place
    to forget."""


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
    schema_enforced: bool = False
    """Whether the schema was a decoding constraint or only a description in the prompt. Worth
    recording per call: it is the difference between "cannot produce invalid JSON" and "was asked
    nicely", and it changes what a retry means."""
    num_ctx: int | None = None


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
        messages: list[Message],
        *,
        budget_scopes: list[tuple[Scope, str]],
        estimated_tokens: int = 2000,
        options: GatewayOptions | None = None,
    ) -> GatewayResult:
        opts = options or GatewayOptions()
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
        enforce = bool(opts.structured_output and endpoint.supports_json_schema)
        convo = _with_system_instruction([*messages], opts.system_instruction)
        if not enforce:
            # The fallback: describe the schema in the system message and hope. Kept for providers
            # that cannot constrain decoding — it is what every call used to do, and it is why a
            # reasoning model's <think> block had to be stripped out of the answer afterwards.
            sys_suffix = (
                "\nReturn ONLY a JSON object matching this JSON Schema"
                " (no prose, no code fences):\n" + str(schema)
            )
            if convo and convo[0]["role"] == "system":
                convo[0] = {"role": "system", "content": convo[0]["content"] + sys_suffix}
            else:
                convo.insert(0, {"role": "system", "content": sys_suffix})
        kwargs: dict[str, Any] = {
            "model": f"{endpoint.litellm_prefix}/{model.model_id}",
            "messages": convo,
            "temperature": opts.temperature,
        }
        if enforce:
            # litellm forwards this to Ollama's own top-level `format`, which constrains decoding:
            # the model cannot emit anything but a conforming object. Verified 2026-09-08.
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": response_model.__name__, "schema": schema},
            }
        if opts.think is not None and endpoint.supports_think:
            kwargs["think"] = opts.think
        num_ctx = _num_ctx_for(opts, model, endpoint)
        if num_ctx is not None:
            kwargs["num_ctx"] = num_ctx
        if opts.max_tokens is not None:
            kwargs["max_tokens"] = opts.max_tokens
        if opts.keep_alive is not None:
            kwargs["keep_alive"] = opts.keep_alive
        if opts.seed is not None:
            kwargs["seed"] = opts.seed
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
            schema_enforced=enforce,
            num_ctx=num_ctx,
        )


def _num_ctx_for(
    opts: GatewayOptions, model: ModelDescriptor, endpoint: EndpointConfig
) -> int | None:
    """The context window to ask for, clamped to what the model actually has.

    Only for providers that take one. A cloud endpoint sizes its own window and would reject the
    parameter; Ollama silently uses 4096 unless told, which is the truncation this exists to stop.
    """
    if not endpoint.supports_think and not endpoint.supports_json_schema:
        # Neither Ollama-shaped capability: not an Ollama-shaped endpoint either.
        return opts.num_ctx
    wanted = opts.num_ctx or DEFAULT_NUM_CTX
    return min(wanted, model.context_tokens) if model.context_tokens else wanted


def _strip_fences(text: str) -> str:
    t = text.strip()
    # Reasoning models (Qwen3 et al.) prepend a <think>…</think> block before the answer.
    if t.startswith("<think>") and "</think>" in t:
        t = t.split("</think>", 1)[1].strip()
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
