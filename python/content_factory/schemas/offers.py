"""Normalized compute offers: one shape for every supply source, with real billing terms.

Offers come from adapters (fixture adapters by default; live provider/broker adapters are
separate and need operator accounts). An offer records what is actually billable — minimum
duration, rounding increment, separate storage/egress charges — because advertised hourly rates
are not budgets. Unknown reliability is ``None``, never treated as zero interruptions, and
unknown host identity is not evidence of an independent failure domain.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import SchemaModel, VersionedModel


class OfferPrice(SchemaModel):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    usd_per_hour: float = Field(ge=0)  # normalized for comparison; original terms kept below
    original_amount_per_hour: float = Field(ge=0)
    min_billed_s: int = Field(default=0, ge=0)
    billing_increment_s: int = Field(default=1, ge=1)
    storage_usd_per_gb_month: float = Field(default=0, ge=0)
    egress_usd_per_gb: float = Field(default=0, ge=0)
    requests_usd_per_1000: float = Field(default=0, ge=0)
    prepaid_required: bool = False


class ComputeOffer(VersionedModel):
    offer_id: str = Field(min_length=1, max_length=200)
    source_api: str = Field(min_length=1, max_length=80)  # adapter identity, e.g. "fixture"
    provider: str = Field(min_length=1, max_length=80)
    broker: str | None = Field(default=None, max_length=80)
    underlying_host: str | None = Field(default=None, max_length=120)  # None = unknown identity
    region: str = Field(default="unknown", max_length=80)
    retrieved_at: str = Field(min_length=4)  # ISO instant, UTC
    expires_at: str | None = None  # quote TTL; stale offers must be revalidated before purchase
    availability: Literal["available", "limited", "uncertain", "unavailable"] = "uncertain"
    purchase_mode: Literal["on_demand", "spot", "reserved"] = "on_demand"
    gpu_model: str = Field(default="", max_length=80)  # "" = CPU-only offer
    gpu_count: int = Field(default=0, ge=0)
    vram_gb_per_gpu: float = Field(default=0, ge=0)
    interconnect: str = Field(default="unknown", max_length=60)
    cpu_cores: int = Field(default=0, ge=0)
    ram_gb: float = Field(default=0, ge=0)
    disk_gb: float = Field(default=0, ge=0)
    encoders: tuple[str, ...] = ()  # verified/advertised graphics+video encode capabilities
    price: OfferPrice
    provisioning_overhead_s: int = Field(default=0, ge=0)  # image pull + model start, estimated
    interruption_rate_per_hour: float | None = Field(default=None, ge=0)  # None = no evidence
    notes: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _sane(self) -> ComputeOffer:
        if self.gpu_count > 0 and self.vram_gb_per_gpu <= 0:
            msg = "a GPU offer must state per-device VRAM"
            raise ValueError(msg)
        if self.purchase_mode == "spot" and self.availability == "available":
            # Spot stock is inherently uncertain; adapters must not overstate it.
            pass
        return self
