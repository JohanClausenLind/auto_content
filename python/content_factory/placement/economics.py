"""Expected-total-cost placement: estimate every feasible action, choose, explain, reserve.

Accounting identity per candidate:
    expected_total = all_attempt_compute + storage + transfers + requests + licenses + retry
All-attempt compute bills the ACTUAL billable allocation: useful execution plus other billed
time (provisioning, image pull, model load, idle retention, drain), with the offer's minimum
duration and rounding increment applied once. Rates are per allocation — GPU count is never
multiplied in a second time. Completion time is estimated separately: queue delay and failed
provisioning delay delivery without being billed.

A running task is never migrated for a slightly cheaper offer: ``should_migrate`` requires the
expected remaining savings to exceed transfer, restart, and reliability switch costs.
"""

from __future__ import annotations

import hashlib
import math

from content_factory.budgets.ledger import BudgetExceededError, CostLedger, Scope
from content_factory.schemas.placement import (
    CandidateEstimate,
    CostBreakdown,
    PlacementCandidate,
    PlacementDecision,
    PlacementRequest,
    RejectedPlacement,
)

POLICY_VERSION = "0.1.0"


def billed_seconds(
    useful_s: int, other_s: int, *, min_billed_s: int = 0, increment_s: int = 1
) -> int:
    """The seconds actually charged: minimum duration and rounding applied to the allocation."""
    raw = max(useful_s + other_s, min_billed_s)
    return int(math.ceil(raw / increment_s) * increment_s)


def estimate_candidate(c: PlacementCandidate) -> CandidateEstimate:
    billed = billed_seconds(
        c.expected_useful_execution_s,
        c.expected_other_billed_s,
        min_billed_s=c.min_billed_s,
        increment_s=c.billing_increment_s,
    )
    cost = CostBreakdown(
        compute_usd=round(billed / 3600 * c.rate_usd_per_hour, 6),
        storage_usd=c.storage_usd,
        transfer_usd=c.transfer_usd,
        requests_usd=c.requests_usd,
        licenses_usd=c.licenses_usd,
        retry_usd=c.expected_retry_usd,
    )
    completion = c.queue_delay_s + c.expected_other_billed_s + c.expected_useful_execution_s
    return CandidateEstimate(
        candidate_id=c.candidate_id,
        cost=cost,
        total_usd=cost.total_usd(),
        billed_s=billed,
        expected_completion_s=completion,
        completion_p90_s=c.completion_p90_s if c.completion_p90_s is not None else completion,
    )


def should_migrate(
    *, expected_remaining_usd: float, new_total_usd: float, switch_cost_usd: float
) -> bool:
    """Migrate a running task only when remaining savings beat transfer+restart+reliability."""
    return new_total_usd + switch_cost_usd < expected_remaining_usd


def _decision_id(fingerprint: str, decided_at: str) -> str:
    h = hashlib.sha256(f"{fingerprint}|{decided_at}".encode()).hexdigest()[:12]
    return f"pld_{h}"


def choose_placement(
    request: PlacementRequest,
    candidates: list[PlacementCandidate],
    *,
    decided_at: str,
    ledger: CostLedger | None = None,
    policy_version: str = POLICY_VERSION,
) -> PlacementDecision:
    """Deterministic: filter infeasible candidates with reasons, rank the rest by expected total
    cost (completion time, then id, break ties), then atomically reserve budget when a ledger is
    given. Budget failure is a typed pause, never a silent downgrade."""
    rejected: list[RejectedPlacement] = []
    feasible: list[CandidateEstimate] = []
    for c in sorted(candidates, key=lambda c: c.candidate_id):
        if c.kind != "wait_local" and request.vram_gb_required > c.vram_gb:
            rejected.append(
                RejectedPlacement(
                    candidate_id=c.candidate_id,
                    reason=(
                        f"insufficient VRAM: needs {request.vram_gb_required:g} GB, "
                        f"offers {c.vram_gb:g} GB"
                    ),
                )
            )
            continue
        if request.require_reliable and c.spot:
            rejected.append(
                RejectedPlacement(
                    candidate_id=c.candidate_id,
                    reason="reliability requirement excludes interruptible capacity",
                )
            )
            continue
        est = estimate_candidate(c)
        if request.max_usd is not None and est.total_usd > request.max_usd:
            rejected.append(
                RejectedPlacement(
                    candidate_id=c.candidate_id,
                    reason=f"expected ${est.total_usd:.3f} exceeds job ceiling "
                    f"${request.max_usd:.3f}",
                )
            )
            continue
        if request.deadline_s is not None and est.completion_p90_s > request.deadline_s:
            rejected.append(
                RejectedPlacement(
                    candidate_id=c.candidate_id,
                    reason=(
                        f"p90 completion {est.completion_p90_s}s misses the "
                        f"{request.deadline_s}s deadline"
                    ),
                )
            )
            continue
        feasible.append(est)

    if not feasible:
        return PlacementDecision(
            decision_id=_decision_id(request.task_fingerprint, decided_at),
            task_fingerprint=request.task_fingerprint,
            policy_version=policy_version,
            decided_at=decided_at,
            request=request,
            considered=(),
            rejected=tuple(rejected),
            chosen_candidate_id=None,
            outcome="pause_no_candidate",
            explanation="no feasible candidate: " + "; ".join(r.reason for r in rejected[:5]),
        )

    ranked = sorted(feasible, key=lambda e: (e.total_usd, e.expected_completion_s, e.candidate_id))
    chosen = ranked[0]
    by_id = {c.candidate_id: c for c in candidates}
    chosen_kind = by_id[chosen.candidate_id].kind

    reservation_id: str | None = None
    if ledger is not None and request.budget_scope_keys:
        scopes = [(Scope(s), k) for s, k in request.budget_scope_keys]
        try:
            reservation = ledger.reserve(
                chosen.total_usd,
                scopes=scopes,
                purpose=f"placement:{request.task_fingerprint}",
            )
            reservation_id = reservation.reservation_id
        except BudgetExceededError as exc:
            return PlacementDecision(
                decision_id=_decision_id(request.task_fingerprint, decided_at),
                task_fingerprint=request.task_fingerprint,
                policy_version=policy_version,
                decided_at=decided_at,
                request=request,
                considered=tuple(ranked),
                rejected=tuple(rejected),
                chosen_candidate_id=None,
                outcome="pause_budget",
                explanation=f"budget reservation refused: {exc}",
            )

    runner_up = (
        f"; runner-up {ranked[1].candidate_id} ${ranked[1].total_usd:.3f}"
        if len(ranked) > 1
        else ""
    )
    return PlacementDecision(
        decision_id=_decision_id(request.task_fingerprint, decided_at),
        task_fingerprint=request.task_fingerprint,
        policy_version=policy_version,
        decided_at=decided_at,
        request=request,
        considered=tuple(ranked),
        rejected=tuple(rejected),
        chosen_candidate_id=chosen.candidate_id,
        outcome="wait" if chosen_kind == "wait_local" else "dispatch",
        expected_total_usd=chosen.total_usd,
        expected_completion_s=chosen.expected_completion_s,
        confidence=0.9 if request.deadline_s is not None else 0.5,
        budget_reservation_id=reservation_id,
        explanation=(
            f"chose {chosen.candidate_id} ({chosen_kind}) at expected "
            f"${chosen.total_usd:.3f}, completion ~{chosen.expected_completion_s}s"
            f"{runner_up}; {len(rejected)} rejected"
        ),
    )
