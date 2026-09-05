"""Placement contracts: where a workload runs, chosen on expected total cost, explained.

The policy minimizes expected incremental completion cost subject to feasibility (VRAM,
reliability class, deadline confidence) and budget. Every decision persists the candidates it
considered and rejected with reasons — the downstream compute layer executes the approved
choice or asks for a new decision; it never silently reorders by advertised hourly price.
Waiting locally is itself a candidate: with no deadline it may honestly win.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import SchemaModel, VersionedModel

CandidateKind = Literal["local_node", "enrolled_node", "cloud_offer", "wait_local"]


class PlacementRequest(SchemaModel):
    task_fingerprint: str = Field(min_length=1, max_length=200)  # inputs + immutable config hash
    vram_gb_required: float = Field(default=0, ge=0)
    require_reliable: bool = False  # excludes interruptible capacity
    deadline_s: int | None = Field(default=None, ge=1)  # from decision time; None = no deadline
    max_usd: float | None = Field(default=None, ge=0)
    budget_scope_keys: tuple[tuple[str, str], ...] = ()  # (scope, key) pairs for reservation


class PlacementCandidate(SchemaModel):
    """One feasible action with its actual billable terms. ``rate_usd_per_hour`` for local
    candidates is the configured local policy rate (marginal energy, or fully allocated —
    a consistent configurable choice), never silently zero."""

    candidate_id: str = Field(min_length=1, max_length=200)
    kind: CandidateKind
    node_id: str | None = None
    offer_id: str | None = None
    spot: bool = False
    vram_gb: float = Field(default=0, ge=0)
    rate_usd_per_hour: float = Field(ge=0)
    min_billed_s: int = Field(default=0, ge=0)
    billing_increment_s: int = Field(default=1, ge=1)
    expected_useful_execution_s: int = Field(ge=0)
    expected_other_billed_s: int = Field(default=0, ge=0)  # provisioning, pulls, load, idle, drain
    queue_delay_s: int = Field(default=0, ge=0)  # delays delivery without being billed
    transfer_usd: float = Field(default=0, ge=0)
    storage_usd: float = Field(default=0, ge=0)
    requests_usd: float = Field(default=0, ge=0)
    licenses_usd: float = Field(default=0, ge=0)
    expected_retry_usd: float = Field(default=0, ge=0)  # expected retry waste, not double-counted
    cache_state: Literal["cold", "warm", "unknown"] = "unknown"
    completion_p90_s: int | None = Field(default=None, ge=0)  # None = use expected completion
    interruption_rate_per_hour: float | None = Field(default=None, ge=0)
    notes: str = Field(default="", max_length=300)


class CostBreakdown(SchemaModel):
    """expected_total = all-attempt compute + storage + transfers + requests + licenses."""

    compute_usd: float = Field(ge=0)
    storage_usd: float = Field(ge=0)
    transfer_usd: float = Field(ge=0)
    requests_usd: float = Field(ge=0)
    licenses_usd: float = Field(ge=0)
    retry_usd: float = Field(ge=0)

    def total_usd(self) -> float:
        return round(
            self.compute_usd
            + self.storage_usd
            + self.transfer_usd
            + self.requests_usd
            + self.licenses_usd
            + self.retry_usd,
            6,
        )


class CandidateEstimate(SchemaModel):
    candidate_id: str
    cost: CostBreakdown
    total_usd: float = Field(ge=0)
    billed_s: int = Field(ge=0)
    expected_completion_s: int = Field(ge=0)  # queue delay + other + useful (delivery time)
    completion_p90_s: int = Field(ge=0)


class RejectedPlacement(SchemaModel):
    candidate_id: str
    reason: str = Field(min_length=1, max_length=300)


class PlacementDecision(VersionedModel):
    decision_id: str = Field(min_length=1, max_length=200)
    task_fingerprint: str
    policy_version: str = Field(min_length=1, max_length=40)
    decided_at: str = Field(min_length=4)
    request: PlacementRequest
    considered: tuple[CandidateEstimate, ...] = ()
    rejected: tuple[RejectedPlacement, ...] = ()
    chosen_candidate_id: str | None = None
    outcome: Literal["dispatch", "wait", "pause_budget", "pause_no_candidate"]
    expected_total_usd: float | None = Field(default=None, ge=0)
    expected_completion_s: int | None = Field(default=None, ge=0)
    confidence: float = Field(default=0.5, ge=0, le=1)
    budget_reservation_id: str | None = None
    explanation: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _chosen_consistent(self) -> PlacementDecision:
        if self.outcome in {"dispatch", "wait"} and self.chosen_candidate_id is None:
            msg = f"outcome {self.outcome} needs a chosen candidate"
            raise ValueError(msg)
        if self.chosen_candidate_id is not None:
            ids = {e.candidate_id for e in self.considered}
            if self.chosen_candidate_id not in ids:
                msg = "chosen candidate must be among the considered estimates"
                raise ValueError(msg)
        return self
