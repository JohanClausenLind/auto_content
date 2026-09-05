"""Energy contracts: measured watt-hours, tariffs, and attribution that reconciles.

One report covers one metering source for one node and window. A wall meter already includes
GPU/CPU/disk/PSU losses — GPU-only NVML energy is a separate physical metric and must never be
added on top of the same node's wall energy in a financial total. Attribution shares (jobs plus
an explicit idle bucket) must sum to the measured total: nothing is lost, nothing counted twice.
Gaps in sampling are recorded as gaps, not silently interpolated into "measured" energy.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, VersionedModel

EnergySource = Literal["wall_meter", "nvml_gpu", "estimate"]


class PowerSample(SchemaModel):
    at: str = Field(min_length=4)  # ISO instant, UTC
    watts: float = Field(ge=0)
    cumulative_wh: float | None = Field(default=None, ge=0)  # meter counter when available
    source: EnergySource = "estimate"


class Tariff(VersionedModel):
    """All-in price per kWh plus its informational components. Timestamps are stored UTC;
    the billing timezone applies period boundaries. GPU TDP is not a tariff input."""

    tariff_id: str = Field(min_length=1, max_length=80)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    price_per_kwh: float = Field(ge=0)  # all-in
    components: dict[str, float] = Field(default_factory=dict)  # energy/network/tax/standing…
    valid_from: str = Field(min_length=4)
    billing_timezone: str = Field(default="UTC", max_length=64)

    @model_validator(mode="after")
    def _components_within_total(self) -> Tariff:
        if self.components and sum(self.components.values()) - self.price_per_kwh > 1e-9:
            msg = "tariff components exceed the all-in price"
            raise ValueError(msg)
        return self


class EnergyShare(SchemaModel):
    key: str = Field(min_length=1, max_length=120)  # job/attempt id, or "idle"
    kwh: float = Field(ge=0)


class EnergyReport(VersionedModel):
    node_id: OpaqueId
    source: EnergySource
    window_start: str = Field(min_length=4)
    window_end: str = Field(min_length=4)
    kwh_measured: float = Field(ge=0)
    gaps: tuple[tuple[str, str], ...] = ()  # sampling holes: no energy was counted for these
    tariff_id: str | None = None
    cost: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    attribution: tuple[EnergyShare, ...] = ()

    @model_validator(mode="after")
    def _reconciles(self) -> EnergyReport:
        if self.attribution:
            total = sum(s.kwh for s in self.attribution)
            if abs(total - self.kwh_measured) > 1e-6:
                msg = (
                    f"attribution sums to {total:.6f} kWh but the window measured "
                    f"{self.kwh_measured:.6f} kWh — allocation must reconcile exactly"
                )
                raise ValueError(msg)
        if (self.cost is None) != (self.currency is None):
            msg = "cost and currency come together"
            raise ValueError(msg)
        return self
