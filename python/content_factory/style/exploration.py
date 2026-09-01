"""Style explore/exploit (23.4): consistency first, exploration on purpose, statistics honest.

Platform data is observational: no random assignment, topic/timing confound everything. ADOPT
needs a minimum sample and a conservative sequential comparison; every conclusion is labeled
observational; adopted winners are re-tested because audiences drift."""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum


@dataclass(frozen=True)
class ExplorationPolicy:
    rate: float = 0.1  # ~every 10th post, jittered so it is not mechanical
    cooldown_days: int = 21
    min_samples_before_adopt: int = 5
    confidence_margin: float = 0.15  # conservative relative lift required
    seed: str = "channel"


def should_explore(
    policy: ExplorationPolicy, post_index: int, *, last_exploration_index: int | None
) -> bool:
    """Deterministic jittered cadence: on average every 1/rate posts, never two in a row."""
    if last_exploration_index is not None and post_index - last_exploration_index < 2:
        return False
    rng = random.Random(
        int(hashlib.sha256(f"{policy.seed}|{post_index}".encode()).hexdigest()[:12], 16)
    )
    return rng.random() < policy.rate


class ExperimentState(StrEnum):
    explore = "EXPLORE"
    observe = "OBSERVE"
    retest = "RETEST"
    adopted = "ADOPT"
    retired = "RETIRE"
    inconclusive = "INCONCLUSIVE"


@dataclass(frozen=True)
class Observation:
    post_id: str
    variant: str  # "baseline" | "candidate"
    primary_metric: float
    guardrail_ok: bool
    observed_on: date


@dataclass
class StyleExperiment:
    experiment_id: str
    asset_class: str  # thumbnail | hook | title | caption | posting_time
    candidate_family: str
    baseline_family: str
    policy: ExplorationPolicy
    state: ExperimentState = ExperimentState.explore
    observations: list[Observation] = field(default_factory=list)
    last_test_on: date | None = None
    conclusion: str = ""

    def record(self, obs: Observation) -> None:
        self.observations.append(obs)
        self.last_test_on = obs.observed_on
        if self.state == ExperimentState.explore:
            self.state = ExperimentState.observe

    def can_retest(self, today: date) -> bool:
        return self.last_test_on is not None and today - self.last_test_on >= timedelta(
            days=self.policy.cooldown_days
        )

    def evaluate(self, today: date) -> dict:
        """Conservative sequential comparison on matched posts. Observational, always."""
        cand = [o.primary_metric for o in self.observations if o.variant == "candidate"]
        base = [o.primary_metric for o in self.observations if o.variant == "baseline"]
        guardrail_breach = any(
            not o.guardrail_ok for o in self.observations if o.variant == "candidate"
        )
        n = min(len(cand), len(base))
        label = "observational — no random assignment; topic and timing confound this comparison"
        if guardrail_breach:
            self.state = ExperimentState.retired
            self.conclusion = f"candidate breached a guardrail metric; retired. ({label})"
        elif n < self.policy.min_samples_before_adopt:
            self.state = (
                ExperimentState.retest if self.can_retest(today) else ExperimentState.observe
            )
            self.conclusion = f"{n}/{self.policy.min_samples_before_adopt} matched samples; keep observing. ({label})"  # noqa: E501
        else:
            mean_c = sum(cand) / len(cand)
            mean_b = sum(base) / len(base)
            spread = _pooled_spread(cand, base)
            lift = (mean_c - mean_b) / mean_b if mean_b else 0.0
            if lift >= self.policy.confidence_margin and (mean_c - mean_b) > spread:
                self.state = ExperimentState.adopted
                self.conclusion = f"candidate ahead by {lift:.0%} over {n} matched pairs (beyond spread {spread:.3f}); adopted with periodic retests. ({label})"  # noqa: E501
            elif lift <= -self.policy.confidence_margin and (mean_b - mean_c) > spread:
                self.state = ExperimentState.retired
                self.conclusion = f"candidate behind by {abs(lift):.0%}; retired. ({label})"
            else:
                self.state = ExperimentState.inconclusive
                self.conclusion = f"difference {lift:+.0%} within noise (spread {spread:.3f}); parked, may re-queue. ({label})"  # noqa: E501
        return {
            "state": self.state.value,
            "conclusion": self.conclusion,
            "samples": {"candidate": len(cand), "baseline": len(base)},
            "intervals": {
                "candidate_mean": round(sum(cand) / len(cand), 4) if cand else None,
                "baseline_mean": round(sum(base) / len(base), 4) if base else None,
                "pooled_spread": round(_pooled_spread(cand, base), 4) if cand and base else None,
            },
            "label": "observational",
        }


def _pooled_spread(a: list[float], b: list[float]) -> float:
    def sd(xs: list[float]) -> float:
        if len(xs) < 2:
            return 0.0
        m = sum(xs) / len(xs)
        return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))

    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("inf")
    return math.sqrt(sd(a) ** 2 / na + sd(b) ** 2 / nb) * 2  # ~conservative 2-SE band
