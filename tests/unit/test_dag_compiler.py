from __future__ import annotations

from content_factory.deliverables.dag_compiler import compile_dag
from content_factory.schemas import content
from content_factory.schemas.dag import Executor, Stage
from content_factory.schemas.fixtures import WS, sample_brief, sample_campaign

AUDIO_VIDEO = {Stage.lock_script, Stage.synthesize_narration, Stage.align_words, Stage.compile_captions, Stage.mix_audio, Stage.compile_timeline, Stage.render_scenes, Stage.compose_video}


def _campaign(*deliverables: content.ContentDeliverable) -> content.ContentCampaign:
    return content.ContentCampaign(campaign_id="cmp_test00000001", workspace_id=WS, brief=sample_brief(), deliverables=deliverables)


def test_image_only_job_invokes_no_audio_or_video_stages() -> None:
    dag = compile_dag(_campaign(content.SingleImagePostSpec(deliverable_id="dlv_image0000001", title="card")))
    assert dag.stages().isdisjoint(AUDIO_VIDEO)
    assert {Stage.compile_artboards, Stage.render_static, Stage.qc_deliverable, Stage.originality_gate} <= dag.stages()
    reasons = {p.stage: p.reason for p in dag.pruned}
    assert Stage.synthesize_narration in reasons and "branch" in reasons[Stage.synthesize_narration]
    assert Stage.compile_destination_packages in reasons  # create-only: no destinations


def test_article_only_job_has_no_timeline_and_has_link_check() -> None:
    dag = compile_dag(_campaign(content.ArticleSpec(deliverable_id="dlv_article00001", title="a")))
    assert Stage.compile_timeline not in dag.stages() and Stage.render_static not in dag.stages()
    assert {Stage.draft_article, Stage.link_check, Stage.compile_seo, Stage.export_article} <= dag.stages()


def test_short_video_gets_audio_then_video_in_order_and_shared_stages_once() -> None:
    dag = compile_dag(sample_campaign())
    order = [n.node_id for n in dag.topological()]
    assert order.index("plan_story") < order.index("compile_artboards:dlv_image0000001")
    assert order.index("synthesize_narration:dlv_short0000001") < order.index("align_words:dlv_short0000001") < order.index("compile_timeline:dlv_short0000001") < order.index("compose_video:dlv_short0000001")
    assert sum(1 for n in dag.nodes if n.stage == Stage.plan_story) == 1
    # The image deliverable never depends on any audio/video node.
    image_nodes = {n.node_id: n for n in dag.nodes if n.deliverable_id == "dlv_image0000001"}
    for n in image_nodes.values():
        for dep in n.depends_on:
            assert "dlv_short" not in dep


def test_no_narration_prunes_tts_with_typed_reason() -> None:
    dag = compile_dag(_campaign(content.ShortVideoSpec(deliverable_id="dlv_short0000002", title="silent", narration=False, music=False)))
    assert Stage.synthesize_narration not in dag.stages()
    assert any(p.stage == Stage.synthesize_narration and p.deliverable_id == "dlv_short0000002" for p in dag.pruned)
    assert Stage.compile_timeline in dag.stages()


def test_human_executor_assignment_and_determinism() -> None:
    camp = sample_campaign()
    dag1 = compile_dag(camp, human_stages={"dlv_short0000001": {Stage.synthesize_narration}})
    dag2 = compile_dag(camp, human_stages={"dlv_short0000001": {Stage.synthesize_narration}})
    assert dag1.content_hash() == dag2.content_hash()
    node = next(n for n in dag1.nodes if n.node_id == "synthesize_narration:dlv_short0000001")
    assert node.executor == Executor.human


def test_approved_copy_transform_prunes_research() -> None:
    brief = sample_brief().model_copy(update={"input_mode": content.InputMode.approved_copy_transform, "pasted_copy": "Approved text."})
    camp = content.ContentCampaign(campaign_id="cmp_test00000002", workspace_id=WS, brief=brief, deliverables=(content.TextPostSpec(deliverable_id="dlv_text00000001", title="t"),))
    dag = compile_dag(camp)
    assert Stage.research not in dag.stages() and Stage.verify_claims not in dag.stages()
    assert any(p.stage == Stage.research for p in dag.pruned)
