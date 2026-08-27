"""Candidate selection and admission (18.5) — deterministic policy over typed inputs.

Given a skill, a policy, the model catalog, hardware inventory, budget headroom, and evaluation
approvals, produce an :class:`ExecutionDecision`. Never routes on a model's self-reported
confidence; never lets ``local_only`` reach the cloud; never dispatches below the quality floor.
"""

from __future__ import annotations

from collections.abc import Iterable

from content_factory.schemas.hardware import HardwareInventory
from content_factory.schemas.skills import (
    ExecutionDecision,
    ExecutionLocation,
    ExecutionPolicy,
    HardwareFit,
    ModelDescriptor,
    PolicyKind,
    RejectedCandidate,
    SkillManifest,
)

_LOCAL = {
    ExecutionLocation.local_cpu,
    ExecutionLocation.local_gpu,
    ExecutionLocation.tailnet_worker,
}


def hardware_fit(
    model: ModelDescriptor, hw: HardwareInventory, headroom_ratio: float
) -> HardwareFit:
    if model.location != ExecutionLocation.local_gpu:
        return HardwareFit(fits=True, reason="no local GPU requirement")
    if not hw.gpus:
        return HardwareFit(fits=False, reason="no GPU present")
    need = model.vram_bytes_estimate or 0
    best = max(g.vram_bytes for g in hw.gpus)
    usable = int(best * (1 - headroom_ratio))
    if need > usable:
        return HardwareFit(
            fits=False,
            reason=f"needs {need} B VRAM; {usable} B usable after {headroom_ratio:.0%} headroom",
            headroom_bytes=usable - need,
        )
    return HardwareFit(
        fits=True, reason="fits within calibrated VRAM", headroom_bytes=usable - need
    )


def decide(
    skill: SkillManifest,
    policy: ExecutionPolicy,
    catalog: Iterable[ModelDescriptor],
    hw: HardwareInventory,
    *,
    budget_remaining_usd: float,
    estimated_tokens: int = 2000,
    headroom_ratio: float = 0.12,
) -> ExecutionDecision:
    rejected: list[RejectedCandidate] = []
    candidates: list[tuple[ModelDescriptor, float]] = []
    for m in catalog:
        if skill.required_models and m.alias not in skill.required_models:
            continue
        if policy.kind == PolicyKind.pinned and m.alias != policy.pinned_alias:
            rejected.append(RejectedCandidate(alias=m.alias, reason="not the pinned alias"))
            continue
        if m.location not in skill.permitted_locations:
            rejected.append(
                RejectedCandidate(
                    alias=m.alias, reason=f"location {m.location} not permitted by skill"
                )
            )
            continue
        if (
            policy.kind in {PolicyKind.local_only, PolicyKind.prefer_local, PolicyKind.quality_auto}
            and m.location == ExecutionLocation.cloud
            and policy.kind == PolicyKind.local_only
        ):
            rejected.append(RejectedCandidate(alias=m.alias, reason="local_only forbids cloud"))
            continue
        if policy.kind == PolicyKind.cloud_only and m.location in _LOCAL:
            rejected.append(RejectedCandidate(alias=m.alias, reason="cloud_only forbids local"))
            continue
        if policy.allowed_providers and m.provider not in policy.allowed_providers:
            rejected.append(
                RejectedCandidate(alias=m.alias, reason=f"provider {m.provider} not allowed")
            )
            continue
        if not m.commercial_use and skill.permissions.external_side_effects:
            rejected.append(RejectedCandidate(alias=m.alias, reason="license forbids this use"))
            continue
        if skill.quality_floor is not None and skill.skill_id not in m.approved_for_skills:
            rejected.append(
                RejectedCandidate(
                    alias=m.alias, reason="no passed evaluation for this skill (quality floor)"
                )
            )
            continue
        fit = hardware_fit(m, hw, headroom_ratio)
        if not fit.fits:
            rejected.append(RejectedCandidate(alias=m.alias, reason=fit.reason))
            continue
        cost = 0.0
        if m.location == ExecutionLocation.cloud:
            per_in = m.usd_per_million_input_tokens or 0
            per_out = m.usd_per_million_output_tokens or 0
            cost = (estimated_tokens / 1e6) * (per_in + per_out)
            if policy.max_external_usd is not None and cost > policy.max_external_usd:
                rejected.append(
                    RejectedCandidate(
                        alias=m.alias, reason=f"estimated ${cost:.4f} exceeds policy cap"
                    )
                )
                continue
            if cost > budget_remaining_usd:
                rejected.append(
                    RejectedCandidate(alias=m.alias, reason="insufficient reserved budget")
                )
                continue
        candidates.append((m, cost))

    if not candidates:
        # Spending limits take precedence in the explanation (they are release blockers), then
        # quality floors, then plain unavailability.
        if any("budget" in r.reason or "policy cap" in r.reason for r in rejected):
            pause = "pause_budget"
        elif any("quality floor" in r.reason for r in rejected):
            pause = "pause_insufficient_quality"
        else:
            pause = "pause_no_candidate"
        return ExecutionDecision(
            skill_id=skill.skill_id,
            skill_version=skill.version,
            policy=policy,
            chosen_alias=None,
            chosen_location=None,
            estimated_usd=0.0,
            rejected=tuple(rejected),
            fallback_used=False,
            outcome=pause,  # type: ignore[arg-type]
            explanation="No eligible candidate: "
            + "; ".join(f"{r.alias}: {r.reason}" for r in rejected),
        )

    def rank(item: tuple[ModelDescriptor, float]) -> tuple[int, float]:
        m, cost = item
        local_first = 0 if m.location in _LOCAL else 1
        if policy.kind in {PolicyKind.prefer_local, PolicyKind.local_only}:
            return (local_first, cost)
        if policy.kind == PolicyKind.cloud_only:
            return (0, cost)
        return (0, cost)  # quality_auto: all candidates already meet the floor; cheapest wins

    chosen, cost = sorted(candidates, key=rank)[0]
    fallback = chosen.location == ExecutionLocation.cloud and policy.kind == PolicyKind.prefer_local
    return ExecutionDecision(
        skill_id=skill.skill_id,
        skill_version=skill.version,
        policy=policy,
        chosen_alias=chosen.alias,
        chosen_location=chosen.location,
        estimated_usd=round(cost, 6),
        rejected=tuple(rejected),
        fallback_used=fallback,
        outcome="dispatch",
        explanation=f"{chosen.alias} at {chosen.location} (${cost:.4f} est.); rejected {len(rejected)} candidate(s)",  # noqa: E501
    )
