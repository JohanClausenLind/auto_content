"""Default model catalog: the local models this machine actually serves via Ollama.

The catalog is data — descriptors here are the operator's declared inventory, and routing
(models.routing) still decides per call from policy, hardware, and budget. The primary local
text model is the operator's qwen38-ridge build; qwen3:8b is the smaller fallback."""

from __future__ import annotations

from content_factory.budgets.ledger import CostLedger
from content_factory.config import Settings, get_settings
from content_factory.models.gateway import EndpointConfig, ModelGateway
from content_factory.schemas.skills import ExecutionLocation, ModelDescriptor

GIB = 1024**3


def default_catalog() -> list[ModelDescriptor]:
    return [
        ModelDescriptor(
            alias="local_structured",
            provider="ollama",
            model_id="qwen38-ridge:latest",
            license="Apache-2.0",  # Qwen3 derivative
            commercial_use=True,
            location=ExecutionLocation.local_gpu,
            vram_bytes_estimate=14 * GIB,  # 12.6 GB weights + KV cache headroom
            context_tokens=32768,
            capabilities=("structured_output", "reasoning"),
            fallback_aliases=("local_structured_small",),
        ),
        ModelDescriptor(
            alias="local_structured_small",
            provider="ollama",
            model_id="qwen3:8b",
            license="Apache-2.0",
            commercial_use=True,
            location=ExecutionLocation.local_gpu,
            vram_bytes_estimate=6 * GIB,
            context_tokens=32768,
            capabilities=("structured_output", "reasoning"),
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
