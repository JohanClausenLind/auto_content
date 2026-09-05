"""Default model catalog: the local models this machine actually serves via Ollama.

The catalog is data — descriptors here are the operator's declared inventory, and routing
(models.routing) still decides per call from policy, hardware, and budget. The primary local
text model is the operator's qwen38-ridge build; qwen3:8b is the smaller fallback, and the
Qwen3.6-27B Heretic build is the quality tier the 2026-09-05 model-role decision queued.

Every field below that describes a weight — ``revision``, ``quantization``, ``context_tokens``,
``vram_bytes_estimate`` — was read off ``ollama show`` on 2026-09-08 rather than remembered. Two of
them had been wrong: ridge was recorded at a 32768 context (it declares 262144) and its quant was
not recorded at all, which matters because it is **IQ2_M** — a two-bit quantisation. That is the
whole reason the Heretic tier exists: same parameter count, Q4_K_M, 17 GB against 12 GB."""

from __future__ import annotations

from content_factory.budgets.ledger import CostLedger
from content_factory.config import Settings, get_settings
from content_factory.models.gateway import EndpointConfig, ModelGateway
from content_factory.schemas.skills import ExecutionLocation, ModelDescriptor

GIB = 1024**3


HERETIC_MODEL_ID = (
    "hf.co/DavidAU/Qwen3.6-27B-Heretic-Uncensored-FINETUNE-NEO-CODE-Di-IMatrix-MAX-GGUF:Q4_K_M"
)
"""The quality tier. Long, and left verbatim: it is the tag Ollama answers to, and shortening it
here would mean a request that resolves to nothing."""


def default_catalog() -> list[ModelDescriptor]:
    return [
        ModelDescriptor(
            alias="local_structured",
            provider="ollama",
            model_id="qwen38-ridge:latest",
            revision="8616ca6ccf4c",
            quantization="IQ2_M",
            license="Apache-2.0",  # Qwen3 derivative
            commercial_use=True,
            location=ExecutionLocation.local_gpu,
            vram_bytes_estimate=14 * GIB,  # 12 GB weights + KV cache headroom
            # What the model declares (qwen35, 27.3B). The window actually *used* is
            # GatewayOptions.num_ctx, because a 262k KV cache does not fit on a 24 GB card
            # alongside anything else; this number is the ceiling that clamps it.
            context_tokens=262144,
            capabilities=("structured_output", "reasoning", "tools"),
            fallback_aliases=("local_structured_small",),
        ),
        ModelDescriptor(
            alias="local_structured_small",
            provider="ollama",
            model_id="qwen3:8b",
            revision="500a1f067a9f",
            quantization="Q4_K_M",
            license="Apache-2.0",
            commercial_use=True,
            location=ExecutionLocation.local_gpu,
            vram_bytes_estimate=6 * GIB,
            context_tokens=40960,
            capabilities=("structured_output", "reasoning", "tools"),
        ),
        ModelDescriptor(
            # The quality tier the 2026-09-05 decision queued: "qwen38-ridge stays primary;
            # Ollama Qwen3.6-27B-Heretic Q4_K_M added as a quality tier". Same parameter count as
            # the primary at a four-bit quant instead of a two-bit one, so it is the tier to reach
            # for when a two-bit 27B is the thing producing the wrong answer — not when the prompt
            # is. It is 17 GB, so it does not share the card with HiDream or LTX: a lane that
            # wants it should ask for it and then let services.local free the GPU.
            alias="local_structured_quality",
            provider="ollama",
            model_id=HERETIC_MODEL_ID,
            revision="b4e3402d4cb0",
            quantization="Q4_K_M",
            # A community fine-tune of Qwen3 (Apache-2.0 upstream) with no separate licence file
            # published on the repo. Recorded as the derivative it is rather than as clean
            # Apache-2.0, and commercial_use stays false until somebody reads the model card.
            license="Qwen3 derivative (Apache-2.0 upstream); DavidAU fine-tune, licence unstated",
            commercial_use=False,
            location=ExecutionLocation.local_gpu,
            vram_bytes_estimate=19 * GIB,  # 17 GB weights + KV cache headroom
            context_tokens=262144,
            # `ollama show` reports a clip projector on this build, so it can take images. Declared
            # because it is true, not because anything here uses it yet.
            capabilities=("structured_output", "reasoning", "tools", "vision"),
            fallback_aliases=("local_structured",),
        ),
    ]


def default_endpoints(settings: Settings | None = None) -> dict[str, EndpointConfig]:
    settings = settings or get_settings()
    return {
        "ollama": EndpointConfig(
            provider="ollama",
            # ollama_chat uses /api/chat, which keeps Qwen3 reasoning in a separate `thinking`
            # field instead of inlining <think> blocks into the content.
            litellm_prefix="ollama_chat",
            api_base=settings.providers.ollama.endpoint,
            is_cloud=False,
            # Both verified on this host on 2026-09-08 against litellm 1.99.0 and the running
            # Ollama, rather than assumed from documentation: the JSON Schema arrives as Ollama's
            # own `format` and constrains decoding (a nested $defs/$ref schema validated first
            # time), and `think` arrives as a top-level parameter.
            supports_json_schema=True,
            supports_think=True,
        ),
    }


def build_gateway(
    settings: Settings | None = None,
    *,
    ledger: CostLedger | None = None,
) -> ModelGateway:
    """The runtime gateway: local Ollama models only until the operator adds cloud endpoints."""
    return ModelGateway(
        catalog=default_catalog(),
        endpoints=default_endpoints(settings),
        ledger=ledger or CostLedger(),
    )
