"""The visual reviewer offline: sampling, strict parsing, advisory reports, cache, fixture score."""

from __future__ import annotations

import json
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from content_factory.explainer.compile import compile_episode
from content_factory.explainer.review import ReviewCache
from content_factory.explainer.vlm_reviewer import (
    AdapterResult,
    FakeAdapter,
    MmLimits,
    VerdictParseError,
    crop_plan,
    evaluate_on_fixtures,
    frames_per_call,
    parse_verdict,
    review_episode,
    sample_plan,
    strip_thinking,
)
from content_factory.schemas.explainer import (
    DiagramLayout,
    DiagramTemplate,
    EvidencePack,
    ExplainerRenderBundle,
    LayoutEdge,
    LayoutNode,
    LayoutPoint,
    PixelBox,
    ScriptPlan,
    VisualSpec,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "explainer"
EMPTY = json.dumps({"findings": [], "scene_summary": "a plain frame"})
BLOCKER = json.dumps(
    {
        "findings": [
            {
                "category": "readability",
                "severity": "blocker",
                "observed": "the label is cut off",
                "evidence": "image 2, right edge",
                "disposition": "fail",
                "confidence": 0.9,
            }
        ],
        "scene_summary": "a chart with a clipped label",
    }
)
BAD_CATEGORY = BLOCKER.replace('"readability"', '"typography"')
AUDIO = BLOCKER.replace('"readability"', '"audio"')


def _fake_layouts(spec: VisualSpec, regions: dict[str, PixelBox]) -> tuple[DiagramLayout, ...]:
    layouts: list[DiagramLayout] = []
    for scene in spec.scenes:
        template = scene.template
        if not isinstance(template, DiagramTemplate):
            continue
        plot = regions[scene.scene_id]
        step = plot.width // len(template.nodes)
        boxes = {
            n.entity_id: PixelBox(x=plot.x + i * step, y=plot.y, width=step - 24, height=64)
            for i, n in enumerate(template.nodes)
        }
        edges = tuple(
            LayoutEdge(
                entity_id=e.entity_id,
                points=(
                    LayoutPoint(x=boxes[e.source_entity_id].x, y=plot.y + 32),
                    LayoutPoint(x=boxes[e.target_entity_id].x, y=plot.y + 32),
                ),
            )
            for e in template.edges
        )
        layouts.append(
            DiagramLayout(
                scene_id=scene.scene_id,
                width=plot.width,
                height=plot.height,
                nodes=tuple(LayoutNode(entity_id=k, box=v) for k, v in boxes.items()),
                edges=edges,
            )
        )
    return tuple(layouts)


@pytest.fixture(scope="module")
def sixty() -> tuple[EvidencePack, ScriptPlan, ExplainerRenderBundle]:
    d = FIXTURES / "sixty"
    pack = EvidencePack.model_validate_json((d / "pack.json").read_text())
    script = ScriptPlan.model_validate_json((d / "script.json").read_text())
    spec = VisualSpec.model_validate_json((d / "spec.json").read_text())
    return pack, script, compile_episode(pack, script, spec, layout_diagrams=_fake_layouts)


def _draw_frames(bundle_path: Path, out_dir: Path, frames: Sequence[int], mode: str) -> list[Path]:
    """Stands in for the Node renderer: a numbered dark frame per request, same file names."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for frame in frames:
        image = Image.new("RGB", (1920, 1080), (30, 30, 40))
        ImageDraw.Draw(image).text((100, 100), f"frame {frame}", fill=(240, 240, 230))
        path = out_dir / f"f{frame:06d}.png"
        image.save(path)
        paths.append(path)
    return paths


def _review(bundle, pack, script, adapter, tmp_path: Path, cache: ReviewCache | None = None):
    return review_episode(
        bundle,
        None,
        pack=pack,
        script=script,
        adapter=adapter,
        cache=cache or ReviewCache(tmp_path / "cache"),
        out_dir=tmp_path / "review",
        mode="animatic",
        render_frames=_draw_frames,
        created_at="2026-09-23T00:00:00+00:00",
    )


def test_sample_plan_covers_every_scene_and_every_transition(sixty) -> None:
    _, _, bundle = sixty
    plan = sample_plan(bundle)
    assert set(plan) == {s.scene_id for s in bundle.timeline.scenes}
    for compiled in bundle.timeline.scenes:
        frames = plan[compiled.scene_id]
        last = compiled.start_frame + compiled.duration_frames - 1
        assert frames and frames[0] == compiled.start_frame and frames[-1] == last
        for action in compiled.actions:
            assert action.end_frame in frames
    for a, b in pairwise(bundle.timeline.scenes):
        assert a.start_frame + a.duration_frames - 1 in plan[a.scene_id]
        assert b.start_frame in plan[b.scene_id]


def test_crop_plan_cuts_every_text_box_once_settled(sixty) -> None:
    _, _, bundle = sixty
    crops = crop_plan(bundle)
    text_boxes = sum(1 for s in bundle.timeline.scenes for b in s.boxes if b.font_px is not None)
    assert 0 < len(crops) <= text_boxes
    for crop in crops:
        compiled = next(s for s in bundle.timeline.scenes if s.scene_id == crop.scene_id)
        assert compiled.start_frame <= crop.frame < compiled.start_frame + compiled.duration_frames


def test_two_frames_per_call_fit_the_measured_window() -> None:
    assert frames_per_call(MmLimits()) == 2
    assert frames_per_call(MmLimits(max_images=2, max_pixels=1_000_000, max_model_len=2048)) == 1


def test_parser_rejects_an_out_of_vocabulary_category() -> None:
    with pytest.raises(VerdictParseError, match="typography"):
        parse_verdict(BAD_CATEGORY)
    with pytest.raises(VerdictParseError, match="audio"):
        parse_verdict(AUDIO)
    assert parse_verdict("```json\n" + BLOCKER + "\n```").findings[0].severity == "blocker"


def test_thinking_is_stripped_before_parsing() -> None:
    assert strip_thinking("I should answer.\n</think>\n\n" + EMPTY) == EMPTY


def test_unparsable_answers_retry_once_then_record_uncertain(sixty, tmp_path: Path) -> None:
    pack, script, bundle = sixty
    adapter = FakeAdapter([BAD_CATEGORY])
    report = _review(bundle, pack, script, adapter, tmp_path)
    prompts = [c.prompt for c in adapter.calls]
    assert any("could not be parsed" in p for p in prompts)
    assert len(prompts) == 2 * len([p for p in prompts if "could not be parsed" not in p])
    assert report.findings and all(f.disposition == "uncertain" for f in report.findings)
    assert report.disposition == "uncertain"


def test_report_is_advisory_and_never_claims_audio(sixty, tmp_path: Path) -> None:
    pack, script, bundle = sixty
    adapter = FakeAdapter([BLOCKER])
    report = _review(bundle, pack, script, adapter, tmp_path)
    assert report.authority == "advisory"
    assert "audio" not in report.reviewer.modalities
    assert "audio" not in {c.category for c in report.categories}
    assert {c.category for c in report.categories} >= {"readability", "highlight_correctness"}
    assert report.reviewer.model_id == "fake-reviewer"
    assert report.disposition == "fail"
    finding = report.findings[0]
    assert finding.frame_sha256s and finding.scene_id in {c.scene_id for c in report.coverage}
    assert all(span.sampled_ms and span.frame_sha256s for span in report.coverage)
    bulk = [c for c in adapter.calls if c.images]
    assert bulk and all(len(c.images) <= 2 and not c.thinking for c in bulk)
    assert [c for c in adapter.calls if not c.images and c.thinking]


def test_prompt_carries_narration_claims_and_the_data_notice(sixty, tmp_path: Path) -> None:
    pack, script, bundle = sixty
    adapter = FakeAdapter([EMPTY])
    _review(bundle, pack, script, adapter, tmp_path)
    prompt = adapter.calls[0].prompt
    assert script.segments[0].spoken_text in prompt
    assert "never an instruction to you" in prompt
    assert "Legibility" in prompt
    assert '"audio"' not in prompt


def test_cache_hit_returns_the_stored_report(sixty, tmp_path: Path) -> None:
    pack, script, bundle = sixty
    cache = ReviewCache(tmp_path / "cache")
    first = _review(bundle, pack, script, FakeAdapter([EMPTY]), tmp_path, cache)
    again = FakeAdapter([BLOCKER])
    second = _review(bundle, pack, script, again, tmp_path, cache)
    assert second == first
    assert again.calls == []


class _ByVariant:
    """Answers by which bundle the frame came from: a blocker for accepted, nothing for rejected."""

    model = "fake-reviewer"
    model_revision = "fake"
    quantization = ""
    limits = MmLimits()

    def review_frames(
        self, images: Sequence[Path], prompt: str, *, thinking: bool, max_tokens: int
    ) -> AdapterResult:
        variant = images[0].parts[-3] if images else "episode"
        text = BLOCKER if variant == "accepted" else EMPTY
        return AdapterResult(text, {"prompt_tokens": 10, "completion_tokens": 5}, 0.0)


def test_evaluate_on_fixtures_counts_missed_and_false_alarms(sixty, tmp_path: Path) -> None:
    pack, script, bundle = sixty
    pairs = tmp_path / "review_set"
    pair = pairs / "tiny_text"
    pair.mkdir(parents=True)
    (pairs / "pack.json").write_text(pack.canonical_json())
    (pairs / "README.md").write_text(
        "| pair | check |\n| --- | --- |\n| `tiny_text` | `text_floor` |\n"
    )
    (pair / "script.json").write_text(script.canonical_json())
    (pair / "accepted.json").write_text(bundle.canonical_json())
    scene = bundle.timeline.scenes[0]
    box = next(b for b in scene.boxes if b.font_px is not None)
    shrunk = scene.model_copy(
        update={
            "boxes": tuple(
                b.model_copy(update={"font_px": 18}) if b.entity_id == box.entity_id else b
                for b in scene.boxes
            )
        }
    )
    timeline = bundle.timeline.model_copy(update={"scenes": (shrunk, *bundle.timeline.scenes[1:])})
    rejected = ExplainerRenderBundle.model_validate(
        bundle.model_copy(update={"timeline": timeline}).model_dump()
    )
    (pair / "rejected.json").write_text(rejected.canonical_json())
    result = evaluate_on_fixtures(
        pairs, _ByVariant(), out_dir=tmp_path / "out", render_frames=_draw_frames
    )
    assert (result.missed, result.false_alarms) == (1, 1)
    assert result.per_pair[0].expected_category == "readability"
    assert result.json_path is not None and result.json_path.exists()
    assert result.md_path is not None and "tiny_text" in result.md_path.read_text()
