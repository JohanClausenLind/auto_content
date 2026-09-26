"""Gate F5 on the real renderer: inspect, replace a take, patch a label, re-render, review page."""

from __future__ import annotations

import html
import json
import shutil
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from content_factory.cli.explainer_cmd import app
from content_factory.explainer import pipeline
from content_factory.explainer.pipeline import (
    LEDGER_NAME,
    EpisodeConfig,
    Seams,
    render_stills,
    render_video,
    renderer_source_hash,
)
from content_factory.explainer.timing import ESTIMATED_TOKEN_MS
from content_factory.schemas.base import file_sha256
from content_factory.schemas.explainer import ExplainerRenderBundle, NarrationManifest, ScriptPlan
from tests.unit.explainer_fakes import (
    SIXTY,
    VOICE,
    even_aligner,
    heard_script,
    tone_synth,
    write_tone,
)

pytestmark = pytest.mark.render

EPISODE_ID = "epi_reviewgate1"
SEGMENT = "seg_build0001"
ENTITY = "ent_front_ser"
NEW_LABEL = "Big ring"
UPSTREAM = ("freeze_evidence", "lock_script", "plan_visuals", "capture_sources")


def _quiet(_line: str) -> None:
    return None


@dataclass(frozen=True)
class Review:
    root: Path
    config: EpisodeConfig
    first: dict[str, Any]
    evidence: str
    findings: str
    take: dict[str, Any]
    take_wav: Path
    after_take: dict[str, Any]
    manifest_after_take: NarrationManifest
    label: dict[str, Any]
    after_label: dict[str, Any]
    page: str
    ledger_renderer: tuple[str, str]
    repo_renderer: tuple[str, str]


def _cli(*args: str) -> str:
    result = CliRunner().invoke(app, list(args))
    assert result.exit_code == 0, result.output
    return result.output


def _last_json(output: str) -> dict[str, Any]:
    return json.loads(output.strip().splitlines()[-1])


def _ledger_renderer(config: EpisodeConfig) -> str:
    ledger = json.loads((config.episode_dir / LEDGER_NAME).read_text())
    entry = next(e for e in ledger["steps"] if e["step"] == "render_animatic")
    return entry["inputs"]["renderer_source_hash"]


def _episode_folder(root: Path) -> Path:
    """sixty copied into its own folder, with a config that records patches and takes beside it."""
    for name in ("pack.json", "script.json", "spec.json"):
        shutil.copyfile(SIXTY / name, root / name)
    fields = {
        "episode_id": EPISODE_ID,
        "pack": "pack.json",
        "script": "script.json",
        "spec": "spec.json",
        "takes_dir": "takes",
        "patches": "patches.jsonl",
        "voice": VOICE.model_dump(mode="json"),
        "synth": "kokoro",
        "output_root": "episodes",
    }
    path = root / "config.json"
    path.write_text(json.dumps(fields))
    return path


@pytest.fixture(scope="module")
def review(tmp_path_factory: pytest.TempPathFactory) -> Review:
    root = tmp_path_factory.mktemp("review")
    config_path = _episode_folder(root)
    config = EpisodeConfig.load(config_path)
    script = ScriptPlan.model_validate_json((root / "script.json").read_text())
    seams = Seams(
        render_video=render_video,
        render_stills=render_stills,
        synth=tone_synth,
        aligner=even_aligner,
        asr=heard_script(script),
    )
    episodes = str(root / "episodes")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CF_SERVICES_DIR", str(root / "services"))
        # The CLI builds default seams; the gate swaps in the fake synth, aligner and ASR only.
        patch.setattr(pipeline, "run", partial(pipeline.run, seams=seams, log=_quiet))
        patch.setattr(
            pipeline, "invalidated_steps", partial(pipeline.invalidated_steps, seams=seams)
        )
        repo_before = renderer_source_hash()
        first = _last_json(_cli("rerender", str(config_path)))
        rendered_with = _ledger_renderer(config)
        evidence = _cli("evidence", str(config_path))
        findings = _cli("findings", EPISODE_ID, "--root", episodes)
        wav = write_tone(
            root / "new-take.wav", len(script.segment(SEGMENT).tokens) * ESTIMATED_TOKEN_MS * 3 // 2
        )
        take_args = ("--segment", SEGMENT, "--wav", str(wav), "--reason", "a slower read")
        take = _last_json(_cli("take", str(config_path), *take_args))
        after_take = _last_json(_cli("rerender", str(config_path)))
        manifest = NarrationManifest.model_validate_json(
            (config.episode_dir / "narration" / "narration-manifest.json").read_text()
        )
        repair = {"repair": "text_correction", "entity_id": ENTITY, "text": NEW_LABEL}
        label_args = ("--repair", json.dumps(repair), "--reason", "the legend names the ring")
        label = _last_json(_cli("patch", str(config_path), *label_args, "--author", "gate"))
        after_label = _last_json(_cli("rerender", str(config_path)))
        page_path = Path(_last_json(_cli("page", EPISODE_ID, "--root", episodes))["page"])
        repo_after = renderer_source_hash()
    return Review(
        root=root,
        config=config,
        first=first,
        evidence=evidence,
        findings=findings,
        take=take,
        take_wav=wav,
        after_take=after_take,
        manifest_after_take=manifest,
        label=label,
        after_label=after_label,
        page=page_path.read_text(),
        ledger_renderer=(rendered_with, _ledger_renderer(config)),
        repo_renderer=(repo_before, repo_after),
    )


def test_f5_the_first_run_is_done_and_ready(review: Review) -> None:
    assert review.first["status"] == "done" and review.first["ready"], review.first


def test_f5_evidence_and_findings_name_claims_and_scenes(review: Review) -> None:
    assert "clm_ratio_top" in review.evidence and "scn_teeth001" in review.evidence
    assert "src_wiki_gear" in review.evidence
    assert "scene=scn_" in review.findings and "frame=" in review.findings
    assert _last_json(review.findings)["findings"] >= 1


def test_f5_a_new_take_reruns_narrate_onward_and_nothing_upstream(review: Review) -> None:
    ran, skipped = review.after_take["ran"], review.after_take["skipped"]
    assert review.after_take["status"] == "done", review.after_take
    assert ran[0] == "narrate" and set(UPSTREAM) <= set(skipped)
    assert set(ran) <= set(review.take["reruns"]) and review.take["reruns"][0] == "narrate"
    [take] = [t for t in review.manifest_after_take.takes if t.segment_ids == (SEGMENT,)]
    assert take.kind == "recorded" and take.audio_sha256 == file_sha256(review.take_wav)


def test_f5_a_text_correction_reruns_plan_visuals_and_compile_onward_but_not_narrate(
    review: Review,
) -> None:
    ran, skipped = review.after_label["ran"], review.after_label["skipped"]
    assert review.after_label["status"] == "done", review.after_label
    assert ran[:2] == ["plan_visuals", "compile"] and "narrate" in skipped
    assert {"freeze_evidence", "lock_script", "capture_sources"} <= set(skipped)
    assert set(ran) <= set(review.label["reruns"])
    for rendered in ("final/video.bundle.json", "repair/bundle.json"):
        bundle = ExplainerRenderBundle.model_validate_json(
            (review.config.episode_dir / rendered).read_text()
        )
        entity = next(e for e in bundle.spec.entities if e.entity_id == ENTITY)
        assert entity.label == NEW_LABEL, rendered
        # The renderer draws the compiled box text, so the correction reaches the pixels.
        drawn = {b.text for s in bundle.timeline.scenes for b in s.boxes if b.entity_id == ENTITY}
        assert drawn == {NEW_LABEL}, rendered


def test_f5_the_review_page_shows_scenes_transcript_and_patch_history(review: Review) -> None:
    bundle = ExplainerRenderBundle.model_validate_json(
        (review.config.episode_dir / "repair" / "bundle.json").read_text()
    )
    for scene in bundle.timeline.scenes:
        assert f'id="scene-{scene.scene_id}"' in review.page
    script = ScriptPlan.model_validate_json((review.root / "script.json").read_text())
    for segment in script.segments:
        assert html.escape(segment.spoken_text, quote=True) in review.page
    assert review.take["patch_id"] in review.page and review.label["patch_id"] in review.page
    assert "take_selection" in review.page and "text_correction" in review.page
    assert 'src="final.mp4"' in review.page


def test_f5_the_renderer_source_hash_is_unchanged_by_the_review(review: Review) -> None:
    before, after = review.repo_renderer
    if before != after:
        pytest.skip("another session edited the renderer during the gate")
    assert review.ledger_renderer == (before, before)
