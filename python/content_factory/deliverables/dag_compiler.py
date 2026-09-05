"""Compile a ContentCampaign into a pruned DeliverableDAG (2.12, section 7).

Deterministic: same campaign → same DAG. Only branches the selected deliverables require exist;
everything else is recorded as a typed ``NotRequired`` with its policy reason.
"""

from __future__ import annotations

from content_factory.schemas.content import ContentCampaign
from content_factory.schemas.dag import (
    DeliverableDAG,
    Executor,
    NotRequired,
    ResourceClass,
    Stage,
    StageNode,
)

COMPILER_VERSION = "0.2.0"

_SHARED_CHAIN: tuple[tuple[Stage, ResourceClass], ...] = (
    (Stage.ingest, "control"),
    (Stage.research, "research"),
    (Stage.verify_claims, "inference-llm"),
    (Stage.compile_datasets, "control"),
    (Stage.plan_story, "inference-llm"),
    (Stage.originality_topic, "control"),
    (Stage.preflight, "control"),
)

_BRANCHES: dict[str, tuple[tuple[Stage, ResourceClass, Executor], ...]] = {
    "text": (
        (Stage.write_copy, "inference-llm", Executor.ai),
        (Stage.compile_text_package, "control", Executor.deterministic),
    ),
    "static": (
        (Stage.write_copy, "inference-llm", Executor.ai),
        (Stage.compile_artboards, "control", Executor.deterministic),
        (Stage.render_static, "render-cpu", Executor.deterministic),
    ),
    "carousel": (
        (Stage.write_copy, "inference-llm", Executor.ai),
        (Stage.compile_cards, "control", Executor.deterministic),
        (Stage.render_cards, "render-cpu", Executor.deterministic),
    ),
    "article": (
        (Stage.draft_article, "inference-llm", Executor.ai),
        (Stage.link_check, "research", Executor.deterministic),
        (Stage.compile_seo, "control", Executor.deterministic),
        (Stage.export_article, "control", Executor.deterministic),
    ),
    "newsletter": (
        (Stage.compose_email, "inference-llm", Executor.ai),
        (Stage.compile_email, "render-cpu", Executor.deterministic),
        (Stage.email_preview_qc, "render-cpu", Executor.deterministic),
    ),
    "sequence": (
        # Shots and controls come first: the anchor is conditioned on the rendered control passes
        # (rough RGB, layout boxes, skeleton) and neither compiler needs the generation lock.
        # Retrieval first: a shot can name the mocap clip it was staged from, so the choice has
        # to be made before the shots are planned.
        (Stage.find_reference, "control", Executor.deterministic),
        (Stage.plan_shots, "control", Executor.deterministic),
        (Stage.compile_controls, "render-cpu", Executor.deterministic),
        (Stage.generate_anchor, "inference-image", Executor.ai),
        (Stage.lock_generation, "control", Executor.deterministic),
        (Stage.generate_keyframes, "inference-image", Executor.ai),
        (Stage.drift_qc, "render-cpu", Executor.deterministic),
        (Stage.interpolate, "inference-image", Executor.deterministic),
        (Stage.package_sequence, "render-cpu", Executor.deterministic),
    ),
    "audio": (
        (Stage.write_copy, "inference-llm", Executor.ai),
        (Stage.lock_script, "control", Executor.deterministic),
        (Stage.synthesize_narration, "inference-audio", Executor.ai),
        (Stage.restore_speech, "inference-audio", Executor.deterministic),
        (Stage.align_words, "inference-audio", Executor.deterministic),
        (Stage.compile_captions, "control", Executor.deterministic),
        (Stage.select_music, "control", Executor.deterministic),
        (Stage.mix_audio, "render-cpu", Executor.deterministic),
    ),
    "video": (
        (Stage.compile_timeline, "control", Executor.deterministic),
        (Stage.render_scenes, "render-cpu", Executor.deterministic),
        (Stage.compose_video, "render-cpu", Executor.deterministic),
    ),
}

_TYPE_BRANCHES: dict[str, tuple[str, ...]] = {
    "text_post": ("text",),
    "thread": ("text",),
    "single_image_post": ("static",),
    "infographic": ("static",),
    "cover": ("static",),
    "carousel": ("carousel",),
    "article": ("article",),
    "newsletter": ("newsletter",),
    "image_sequence": ("sequence",),
    "audio_clip": ("audio",),
    "audiogram": ("audio", "video"),
    "short_video": ("audio", "video"),
    "long_video": ("audio", "video"),
}

_TAIL: tuple[tuple[Stage, ResourceClass], ...] = (
    (Stage.qc_deliverable, "render-cpu"),
    (Stage.originality_gate, "control"),
    (Stage.compile_destination_packages, "control"),
    (Stage.package_qc, "render-cpu"),
)


# Stages available to hand-drawn workspace graphs but not part of any standard branch.
_EXTRA_STAGES: dict[Stage, tuple[ResourceClass, Executor]] = {
    Stage.route_shots: ("control", Executor.deterministic),
    Stage.find_reference: ("control", Executor.deterministic),
    Stage.generate_video: ("inference-video", Executor.ai),
    Stage.render_animation: ("render-cpu", Executor.deterministic),
    Stage.fix_video: ("render-gpu", Executor.deterministic),
    Stage.upscale_video: ("render-gpu", Executor.deterministic),
    # Both reviews park on a human: passing measurements is necessary and never sufficient.
    Stage.review_assets: ("control", Executor.human),
    Stage.review_frames: ("control", Executor.human),
    Stage.voice_over: ("inference-audio", Executor.deterministic),
    Stage.sound_design: ("inference-audio", Executor.ai),
}


def stage_defaults() -> dict[Stage, tuple[ResourceClass, Executor]]:
    """Default (resource class, executor) per stage — the same values compile_dag assigns,
    consumed by the workspace-graph compiler so a hand-drawn node runs identically."""
    defaults: dict[Stage, tuple[ResourceClass, Executor]] = {}
    for stage, rc in _SHARED_CHAIN:
        defaults[stage] = (rc, Executor.deterministic)
    for chain in _BRANCHES.values():
        for stage, rc, executor in chain:
            defaults.setdefault(stage, (rc, executor))
    for stage, rc in _TAIL:
        defaults[stage] = (rc, Executor.deterministic)
    defaults.update(_EXTRA_STAGES)
    return defaults


def compile_dag(
    campaign: ContentCampaign, *, human_stages: dict[str, set[Stage]] | None = None
) -> DeliverableDAG:
    """``human_stages`` maps deliverable_id → stages executed by a human (ChannelArchetype)."""
    human_stages = human_stages or {}
    nodes: list[StageNode] = []
    pruned: list[NotRequired] = []

    needs_research = any(d.type not in {"image_sequence"} for d in campaign.deliverables) and (
        bool(campaign.brief.source_urls)
        or bool(campaign.brief.uploaded_source_ids)
        or campaign.brief.input_mode != "approved_copy_transform"
    )
    prev: str | None = None
    for stage, rc in _SHARED_CHAIN:
        if stage in {Stage.research, Stage.verify_claims} and not needs_research:
            pruned.append(
                NotRequired(
                    stage=stage,
                    deliverable_id=None,
                    reason="approved copy transform: operator-approved text needs no new research",
                )
            )
            continue
        if stage == Stage.ingest and not (
            campaign.brief.uploaded_source_ids or campaign.brief.source_urls
        ):
            pruned.append(
                NotRequired(stage=stage, deliverable_id=None, reason="no uploads or URLs to ingest")
            )
            continue
        nodes.append(
            StageNode(
                node_id=stage.value,
                stage=stage,
                depends_on=(prev,) if prev else (),
                resource_class=rc,
            )
        )
        prev = stage.value
    shared_tail = prev or "preflight"

    for d in campaign.deliverables:
        branches = _TYPE_BRANCHES[d.type]
        seen: dict[Stage, str] = {}
        last: str = shared_tail
        for branch in branches:
            for stage, rc, executor in _BRANCHES[branch]:
                if stage in seen:
                    last = seen[stage]
                    continue
                if stage == Stage.synthesize_narration and getattr(d, "narration", True) is False:
                    pruned.append(
                        NotRequired(
                            stage=stage,
                            deliverable_id=d.deliverable_id,
                            reason="deliverable configured without narration",
                        )
                    )
                    continue
                if (
                    stage == Stage.mix_audio
                    and getattr(d, "music", None) is False
                    and getattr(d, "narration", True) is False
                ):
                    pruned.append(
                        NotRequired(
                            stage=stage,
                            deliverable_id=d.deliverable_id,
                            reason="no narration and no music",
                        )
                    )
                    continue
                if stage == Stage.interpolate and getattr(d, "in_between", "none") == "none":
                    pruned.append(
                        NotRequired(
                            stage=stage,
                            deliverable_id=d.deliverable_id,
                            reason="sequence requests keyframes only",
                        )
                    )
                    continue
                node_id = f"{stage.value}:{d.deliverable_id}"
                ex = (
                    Executor.human
                    if stage in human_stages.get(d.deliverable_id, set())
                    else executor
                )
                nodes.append(
                    StageNode(
                        node_id=node_id,
                        stage=stage,
                        deliverable_id=d.deliverable_id,
                        depends_on=(last,),
                        executor=ex,
                        resource_class=rc,
                    )
                )
                seen[stage] = node_id
                last = node_id
        for stage, rc in _TAIL:
            node_id = f"{stage.value}:{d.deliverable_id}"
            if stage == Stage.compile_destination_packages and not d.destinations:
                pruned.append(
                    NotRequired(
                        stage=stage,
                        deliverable_id=d.deliverable_id,
                        reason="no destinations selected (create-only)",
                    )
                )
                continue
            if stage == Stage.package_qc and not d.destinations:
                pruned.append(
                    NotRequired(
                        stage=stage,
                        deliverable_id=d.deliverable_id,
                        reason="no destination packages to check",
                    )
                )
                continue
            nodes.append(
                StageNode(
                    node_id=node_id,
                    stage=stage,
                    deliverable_id=d.deliverable_id,
                    depends_on=(last,),
                    resource_class=rc,
                )
            )
            last = node_id

    # Record explicit non-requirements for the branches no deliverable asked for.
    wanted = {b for d in campaign.deliverables for b in _TYPE_BRANCHES[d.type]}
    for branch, stages in _BRANCHES.items():
        if branch not in wanted:
            for stage, _, _ in stages:
                if all(p.stage != stage for p in pruned) and all(n.stage != stage for n in nodes):
                    pruned.append(
                        NotRequired(
                            stage=stage,
                            deliverable_id=None,
                            reason=f"no selected deliverable needs the {branch} branch",
                        )
                    )

    return DeliverableDAG(
        campaign_id=campaign.campaign_id,
        nodes=tuple(nodes),
        pruned=tuple(pruned),
        compiler_version=COMPILER_VERSION,
    )
