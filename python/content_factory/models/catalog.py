"""Default model catalog: the local models this machine actually serves via Ollama."""

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
            # The declared ceiling; the window used is GatewayOptions.num_ctx, because a 262k KV
            # cache does not fit on a 24 GB card alongside anything else.
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
            # Quality tier (decision 2026-09-05): the primary's parameter count at four bits, for
            # when the two-bit quant is the wrong answer. 17 GB, so it never shares the card.
            alias="local_structured_quality",
            provider="ollama",
            model_id=HERETIC_MODEL_ID,
            revision="b4e3402d4cb0",
            quantization="Q4_K_M",
            # No licence file on the fine-tune's repo, so it is recorded as a derivative and
            # commercial_use stays false until somebody reads the model card.
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
            # Both verified against litellm 1.99.0 and the running Ollama (journal 2026-09-08):
            # the schema arrives as Ollama's `format` and `think` as a top-level parameter.
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
