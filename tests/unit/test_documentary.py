from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.deliverables.dag_compiler import compile_dag
from content_factory.deliverables.documentary import (
    documentary_campaign,
    episode_metadata,
    episode_outline,
    find_documentary,
    plan_shorts,
    short_story_plan,
)
from content_factory.schemas import scenes
from content_factory.schemas.documentary import EpisodeOutline, ShortsPlan
from content_factory.schemas.fixtures import WS, sample_campaign, sample_story_plan
from content_factory.schemas.research import SourceRecord
from content_factory.timeline.compiler import compile_timeline
from content_factory.workflows.stages import StageContext, stage_plan_story

TOPIC = "How undersea cables carry the internet"


def _long_plan(n_beats: int = 12, beat_ms: int = 5000) -> scenes.StoryPlan:
    beats = tuple(
        scenes.VisualBeat(
            beat_id=f"beat_{i:09d}",
            order=i,
            display_text=f"Beat {i} explains one step of the mechanism in a full sentence.",
            claim_ids=(f"clm_{i:010d}",) if i in {2, 3, 7} else (),
            planned_duration_ms=beat_ms,
        )
        for i in range(n_beats)
    )
    scene_list = tuple(
        scenes.CalloutScene(
            scene_id=f"scn_{i:09d}",
            beat_id=f"beat_{i:09d}",
            text=scenes.TextRef(text=f"Visual for beat {i}"),
        )
        for i in range(n_beats)
    )
    return scenes.StoryPlan(
        plan_id="plan_longtest01",
        deliverable_id="dlv_long0000001",
        fps=30,
        width=1920,
        height=1080,
        beats=beats,
        scenes=scene_list,
    )


def test_outline_tiles_duration_and_stays_in_word_band() -> None:
    outline = episode_outline(deliverable_id="dlv_long0000001", target_duration_s=600)
    assert [s.start_s for s in outline.sections] == [0, 20, 50, 180, 390, 495, 555]
    assert outline.sections[-1].end_s == 600
    # ~1,350-1,550 words for a ten-minute episode; the narration remains the actual clock.
    assert 1350 <= outline.total_word_budget() <= 1550
    assert outline.total_word_budget() == 1450  # 600 s at the default 145 wpm, allocated exactly
    # Short episodes still keep every section of the arc.
    tiny = episode_outline(deliverable_id="dlv_long0000001", target_duration_s=60)
    assert len(tiny.sections) == 7
    assert tiny.sections[-1].end_s == 60


def test_documentary_campaign_compiles_to_per_short_branches() -> None:
    campaign = documentary_campaign(workspace_id=WS, topic=TOPIC, minutes=10, shorts=3)
    assert len(campaign.deliverables) == 4
    assert sum(1 for r in campaign.relationships if r.kind == "excerpt_of") == 3
    long_spec, shorts = find_documentary(campaign) or (None, ())
    assert long_spec is not None and long_spec.aspect.value == "16:9" and long_spec.chapters
    assert len(shorts) == 3 and all(s.aspect.value == "9:16" for s in shorts)
    dag = compile_dag(campaign)
    node_ids = {n.node_id for n in dag.nodes}
    for s in shorts:  # every short gets its own narration→timeline→video branch (a re-edit)
        assert f"synthesize_narration:{s.deliverable_id}" in node_ids
        assert f"compile_timeline:{s.deliverable_id}" in node_ids
    # Deterministic: the same inputs are the same campaign, byte for byte.
    again = documentary_campaign(workspace_id=WS, topic=TOPIC, minutes=10, shorts=3)
    assert again.content_hash() == campaign.content_hash()


def test_find_documentary_rejects_non_documentary_campaigns() -> None:
    assert find_documentary(sample_campaign()) is None


def test_plan_shorts_picks_contiguous_claim_dense_windows() -> None:
    plan = _long_plan()
    ids = ["dlv_shortaa0001", "dlv_shortaa0002", "dlv_shortaa0003"]
    shorts = plan_shorts(plan, ids)
    assert isinstance(shorts, ShortsPlan) and len(shorts.excerpts) == 3
    by_id = {b.beat_id: b for b in plan.beats}
    for e in shorts.excerpts:
        assert 20_000 <= e.duration_ms <= 60_000
        orders = [by_id[b].order for b in e.source_beat_ids]
        assert orders == list(range(orders[0], orders[0] + len(orders)))  # contiguous, no crops
    best = next(e for e in shorts.excerpts if e.short_deliverable_id == ids[0])
    claimed = {b for e in shorts.excerpts for b in e.claim_ids}
    assert claimed  # at least one short carries the evidence-dense passage
    assert best.hook_text == by_id[best.source_beat_ids[0]].display_text  # verbatim by default
    assert plan_shorts(plan, ids).content_hash() == shorts.content_hash()


def test_short_story_plan_is_a_vertical_reedit_not_a_crop() -> None:
    plan = _long_plan()
    excerpt = plan_shorts(plan, ["dlv_shortaa0001"]).excerpts[0]
    short = short_story_plan(plan, excerpt)
    assert (short.width, short.height) == (1080, 1920)
    assert [b.order for b in short.beats] == list(range(len(short.beats)))
    assert all(b.measured_start_ms is None and not b.words for b in short.beats)
    assert {s.beat_id for s in short.scenes} == {b.beat_id for b in short.beats}
    # The derived plan is render-ready: it compiles to integer frames like any other plan.
    tl = compile_timeline(short, timeline_id="tl_shorttest01", narrated=False)
    assert tl.total_frames > 0 and tl.width == 1080

    # A rewritten hook is a new statement: it must not inherit the original beat's claim links.
    rewritten = excerpt.model_copy(update={"hook_text": "What actually happens down there?"})
    hooked = short_story_plan(plan, rewritten)
    assert hooked.beats[0].display_text == "What actually happens down there?"
    assert hooked.beats[0].claim_ids == ()
    assert hooked.beats[1].claim_ids == short.beats[1].claim_ids


def test_plan_shorts_falls_back_to_whole_plan_when_long_plan_is_tiny() -> None:
    plan = sample_story_plan()  # ~17 s total, below the 20 s minimum
    shorts = plan_shorts(plan, ["dlv_shortaa0001", "dlv_shortaa0002"])
    for e in shorts.excerpts:
        assert e.source_beat_ids == tuple(b.beat_id for b in plan.beats)


def test_episode_metadata_carries_chapters_sources_and_disclosure() -> None:
    campaign = documentary_campaign(workspace_id=WS, topic=TOPIC, minutes=10, shorts=0)
    outline = episode_outline(
        deliverable_id=campaign.deliverables[0].deliverable_id, target_duration_s=600
    )
    src = SourceRecord(
        source_id="src_cable000001",
        workspace_id=WS,
        canonical_url="https://example.org/cables",
        requested_url="https://example.org/cables",
        final_url="https://example.org/cables",
        title="Submarine cable survey",
        publisher="Example Institute",
        accessed_at="2026-09-04",
        capture_sha256="a" * 64,
        content_type="text/html",
        size_bytes=1000,
    )
    meta = episode_metadata(campaign.brief, outline, [src])
    assert meta.chapters[0].at_s == 0 and len(meta.chapters) == 7
    assert "Chapters:" in meta.description and "Submarine cable survey" in meta.description
    assert meta.synthetic_media_disclosure and meta.disclosure_text in meta.description
    assert meta.tags and all(len(t) >= 4 for t in meta.tags)
    assert len(meta.title_candidates) == 3


def test_stage_plan_story_writes_documentary_artifacts(tmp_path: Path) -> None:
    campaign = documentary_campaign(workspace_id=WS, topic=TOPIC, minutes=10, shorts=3)
    ctx = StageContext(
        workspace_id=WS,
        project_dir=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        campaign=campaign,
        deliverable_id=None,
        quality="smoke",
        dep_outputs={},
    )
    out = stage_plan_story(ctx)
    assert out.facts["outline_sections"] == 7 and out.facts["shorts"] == 3
    outline = EpisodeOutline.model_validate_json((tmp_path / "story" / "outline.json").read_text())
    assert outline.deliverable_id == campaign.deliverables[0].deliverable_id
    shorts = ShortsPlan.model_validate_json((tmp_path / "story" / "shorts.json").read_text())
    for e in shorts.excerpts:
        derived = tmp_path / "story" / "shorts" / f"{e.short_deliverable_id}.plan.json"
        sp = scenes.StoryPlan.model_validate_json(derived.read_text())
        assert (sp.width, sp.height) == (1080, 1920)
    # Rerun is byte-stable (idempotent, cache-friendly).
    assert stage_plan_story(ctx).outputs_hash == out.outputs_hash


def test_stage_plan_story_unchanged_for_non_documentary_campaigns(tmp_path: Path) -> None:
    ctx = StageContext(
        workspace_id=WS,
        project_dir=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        campaign=sample_campaign(),
        deliverable_id=None,
        quality="smoke",
        dep_outputs={},
    )
    out = stage_plan_story(ctx)
    assert out.outputs_hash == sample_story_plan().content_hash()
    assert not (tmp_path / "story" / "outline.json").exists()


def test_short_story_plan_refuses_beats_without_scenes() -> None:
    plan = _long_plan()
    excerpt = plan_shorts(plan, ["dlv_shortaa0001"]).excerpts[0]
    stripped = plan.model_copy(
        update={"scenes": tuple(s for s in plan.scenes if s.beat_id != excerpt.source_beat_ids[0])}
    )
    with pytest.raises(ValueError, match="no scene"):
        short_story_plan(stripped, excerpt)


def test_outline_json_roundtrip(tmp_path: Path) -> None:
    outline = episode_outline(deliverable_id="dlv_long0000001", target_duration_s=480)
    raw = json.loads(outline.model_dump_json())
    assert EpisodeOutline.model_validate(raw).content_hash() == outline.content_hash()


def test_each_short_narrates_its_own_plan_not_the_episode(tmp_path: Path) -> None:
    """STATUS 528 called this "the lane's next smallest task", and it was a resolution bug.

    ``plan_story`` has written a derived per-short plan since 2026-09-04 — a re-edit, never a crop,
    with the measured timings reset and a rewritten hook. Nothing consumed them: every
    per-deliverable stage resolved ``story/plan.json``, so all N shorts locked the LONG script and
    came out as N copies of the same film at a different aspect ratio.
    """
    from content_factory.workflows.stages import _load_story_plan, stage_lock_script

    campaign = documentary_campaign(workspace_id=WS, topic=TOPIC, minutes=10, shorts=3)
    long_id = campaign.deliverables[0].deliverable_id
    shared = StageContext(
        workspace_id=WS,
        project_dir=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        campaign=campaign,
        deliverable_id=None,
        quality="smoke",
        dep_outputs={},
    )
    # A real episode script, so plan_shorts picks three genuinely different windows. On the demo
    # fixture (17 s, four beats) it falls back to the whole plan for every short, which would make
    # "each short has its own script" true and untestable.
    object.__setattr__(shared, "params", {"story": "fixtures/story/wind_2024.json"})
    stage_plan_story(shared)
    shorts = ShortsPlan.model_validate_json((tmp_path / "story" / "shorts.json").read_text())
    long_plan = scenes.StoryPlan.model_validate_json((tmp_path / "story" / "plan.json").read_text())

    def for_deliverable(deliverable_id: str) -> StageContext:
        return StageContext(
            workspace_id=WS,
            project_dir=tmp_path,
            artifacts_dir=tmp_path / "artifacts",
            campaign=campaign,
            deliverable_id=deliverable_id,
            quality="smoke",
            dep_outputs={},
        )

    # The long video still reads the episode plan.
    assert _load_story_plan(for_deliverable(long_id)).content_hash() == long_plan.content_hash()
    assert stage_lock_script(for_deliverable(long_id)).facts["plan"] == "story/plan.json"

    locked: dict[str, tuple[str, ...]] = {}
    for excerpt in shorts.excerpts:
        ctx = for_deliverable(excerpt.short_deliverable_id)
        plan = _load_story_plan(ctx)
        assert plan.deliverable_id == excerpt.short_deliverable_id
        assert (plan.width, plan.height) == (1080, 1920)
        # The excerpt's own headline, and the episode's world.
        assert plan.hook_text == excerpt.hook_text[:120]
        assert plan.visual_subject == long_plan.visual_subject
        out = stage_lock_script(ctx)
        assert out.facts["plan"] == f"story/shorts/{excerpt.short_deliverable_id}.plan.json"
        script = json.loads((ctx.ddir() / "script" / "locked.json").read_text())
        assert script["plan_hash"] == plan.content_hash()
        assert len(script["sentences"]) == len(plan.beats)
        assert script["sentences"][0] == excerpt.hook_text
        locked[excerpt.short_deliverable_id] = tuple(script["sentences"])

    # Three shorts, three different scripts, each locked from its own derived plan. (One short's
    # window happens to cover the whole episode, so its sentences coincide with the episode's —
    # what matters is that it locked them from ITS plan, at 1080x1920, under its own hook.)
    assert len(locked) == 3 and len(set(locked.values())) == 3
