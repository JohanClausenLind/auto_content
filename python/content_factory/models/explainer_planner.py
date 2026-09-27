"""The mechanism-first explainer planner: one prompt, one plan, and the checker as the repair loop.

The prompt and its schema live in ``docs/prompts/explainer-planner/``, where the review loop that
wrote them keeps its rubric and log. This module is the pipeline half: it builds the ``<request>``
block from the campaign, calls the model through the gateway, and runs
``content_factory.explainer.check`` on what comes back.

**The checker is the repair loop, not a gate after the fact.** Eight review rounds showed the same
thing: a careful model following the prompt still misses a counted rule on its first draft (a
structural move 110 words after the last one, a question at word 31), and the checker catches
every one. So the errors go back to the model as a follow-up turn and it returns a corrected
plan, up to ``max_repairs`` times. A plan that still has errors after that is a named failure with
the plan and its report written, never a silently accepted one.

**Full-plan mode only, for now.** The prompt also defines a skeleton-then-batches mode for models
that cannot emit a whole plan at once; it needs the ``<state>`` block computed between batches,
and is not wired yet. The one measured local run produced about 1,000 tokens (STATUS 2026-09-08);
a full plan is several thousand, so which model runs this role matters more here than for the
script writer, and the result records the alias and the token counts of every attempt.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from content_factory.budgets.ledger import Scope
from content_factory.explainer.check import check
from content_factory.models.gateway import GatewayOptions, ModelGateway
from content_factory.schemas.content import ContentCampaign
from content_factory.schemas.explainer import ExplainerPlan
from content_factory.schemas.skills import (
    PRESETS,
    CostEstimator,
    ExecutionLocation,
    ExecutorType,
    Lifecycle,
    SkillManifest,
    SkillPermissions,
)

EXPLAINER_PLANNER_VERSION = "0.1.0"
PROMPT_DIR = Path(__file__).resolve().parents[3] / "docs" / "prompts" / "explainer-planner"
MAX_REPAIRS = 2
REPAIR_ERROR_LINES = 25
"""How many checker errors a repair turn lists. The first ones are the structural ones (schema,
references, transitions); a plan with more than this has bigger problems than the tail."""

_SKILL = SkillManifest(
    skill_id="content.explainer_planner",
    version="0.1.0",
    status=Lifecycle.draft,
    purpose="Plan a mechanism-first explainer: narration, scene graph and beats in one document.",
    input_schema="ContentCampaign",
    output_schema="ExplainerPlan",
    executor=ExecutorType.model_role,
    implementation_ref="content_factory.models.explainer_planner:draft_explainer_plan",
    permitted_locations=(ExecutionLocation.local_gpu, ExecutionLocation.local_cpu),
    required_models=("local_structured", "local_structured_small"),
    # No egress: the planner sees the brief and the claims research already captured.
    permissions=SkillPermissions(network_egress=False),
    license_evidence="Apache-2.0 (Qwen3 derivative, local)",
    cost=CostEstimator(kind="per_token", usd=0),
    timeout_seconds=1800,
    max_retries=1,
)


def prompt_text() -> str:
    return (PROMPT_DIR / "prompt.md").read_text(encoding="utf-8")


def prompt_version() -> str:
    """A digest of the prompt and its schema: a reworded prompt is a new planner."""
    h = hashlib.sha256()
    for name in ("prompt.md", "schema.json"):
        h.update((PROMPT_DIR / name).read_bytes())
    return h.hexdigest()[:12]


def request_block(
    *,
    topic: str,
    target_duration_s: int | None = None,
    audience: str = "",
    source_notes: str = "",
    renderer_constraints: str = "",
) -> str:
    """The ``<request>`` the prompt's ``<inputs>`` section describes. Empty parts are left out,
    because the prompt gives each a default ("If the duration is missing, plan for about 420")."""
    parts = [f"<topic>{topic.strip()}</topic>"]
    if target_duration_s is not None:
        parts.append(f"<target_duration_s>{target_duration_s}</target_duration_s>")
    if audience.strip():
        parts.append(f"<audience>{audience.strip()}</audience>")
    if source_notes.strip():
        parts.append(f"<source_notes>{source_notes.strip()}</source_notes>")
    if renderer_constraints.strip():
        parts.append(f"<renderer_constraints>{renderer_constraints.strip()}</renderer_constraints>")
    return "<request>\n" + "\n".join(parts) + "\n</request>"


def repair_message(errors: list[str]) -> str:
    listed = "\n".join(f"- {e}" for e in errors[:REPAIR_ERROR_LINES])
    more = len(errors) - REPAIR_ERROR_LINES
    tail = f"\n- ...and {more} more of the same kinds" if more > 0 else ""
    return (
        "The plan above fails these checks, which code counts exactly as the prompt states them:\n"
        f"{listed}{tail}\n\n"
        "Return the whole corrected plan as one JSON object. Fix each failure at its cause (for a"
        " pacing gap, add the structural move the narration is about to call for or shorten the"
        " stretch; never insert a move just for the count), and change nothing that already"
        " passes."
    )


@dataclass
class Attempt:
    model: str
    input_tokens: int
    output_tokens: int
    elapsed_s: float
    errors: int
    warnings: int


@dataclass
class ExplainerDraft:
    plan: ExplainerPlan | None
    """The accepted plan, or ``None`` when every attempt failed the checker."""
    last: dict | None = None
    """The last plan the model returned, as written, whether or not it passed."""
    report: dict = field(default_factory=dict)
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def facts(self) -> dict:
        return {
            "planner_version": EXPLAINER_PLANNER_VERSION,
            "prompt_version": prompt_version(),
            "attempts": len(self.attempts),
            "repairs": max(0, len(self.attempts) - 1),
            "errors": len(self.report.get("errors", [])),
            "warnings": len(self.report.get("warnings", [])),
            "model": self.attempts[-1].model if self.attempts else "",
            "input_tokens": sum(a.input_tokens for a in self.attempts),
            "output_tokens": sum(a.output_tokens for a in self.attempts),
            "elapsed_s": round(sum(a.elapsed_s for a in self.attempts), 1),
        }


def campaign_request(
    campaign: ContentCampaign,
    *,
    target_duration_s: int | None,
    claim_statements: Sequence[str] = (),
) -> str:
    """The request for this campaign. Source notes are the operator's pasted copy and the claims
    research verified, which the prompt treats as ground truth; with neither, the planner states
    only well-established facts and lists every one for verification."""
    brief = campaign.brief
    notes = [brief.pasted_copy.strip()] if brief.pasted_copy else []
    notes += [f"- {s}" for s in claim_statements]
    audience = brief.audience if brief.audience.strip().lower() not in {"", "general"} else ""
    return request_block(
        topic=brief.topic,
        target_duration_s=target_duration_s,
        audience=audience,
        source_notes="\n".join(notes),
    )


def draft_explainer_plan(
    request: str,
    *,
    max_repairs: int = MAX_REPAIRS,
    gateway: ModelGateway | None = None,
) -> ExplainerDraft:
    """Plan one explainer from a ``<request>`` block, repairing against the checker.

    Returns the first attempt that passes with zero checker errors, or ``plan=None`` with the last
    attempt and its report when none does. Warnings never block: they are the checker's "look at
    this", not its "this is wrong".
    """
    from content_factory.models.catalog import build_gateway

    gw = gateway or build_gateway()
    messages: list[dict[str, str]] = [
        {"role": "system", "content": prompt_text()},
        {"role": "user", "content": request},
    ]
    draft = ExplainerDraft(plan=None)
    for _ in range(max_repairs + 1):
        result = gw.complete_structured(
            _SKILL,
            PRESETS["private_local"],
            ExplainerPlan,
            messages,
            budget_scopes=[(Scope.monthly, "explainer_planner")],
            estimated_tokens=20000,
            options=GatewayOptions(
                structured_output=True,
                think=False,
                # The prompt is ~6k tokens and a full plan up to ~9k more; a repair turn carries
                # the previous plan as well. 32k is the window the script writer runs in.
                num_ctx=32768,
                keep_alive=0,
            ),
        )
        value = result.value
        assert isinstance(value, ExplainerPlan)
        written = value.model_dump(mode="json", by_alias=True)
        report = check(written)
        draft.last, draft.report = written, report
        draft.attempts.append(
            Attempt(
                model=result.model_alias,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                elapsed_s=result.elapsed_s,
                errors=len(report["errors"]),
                warnings=len(report["warnings"]),
            )
        )
        if report["ok"]:
            draft.plan = value
            return draft
        messages += [
            {"role": "assistant", "content": json.dumps(written)},
            {"role": "user", "content": repair_message(report["errors"])},
        ]
    return draft
