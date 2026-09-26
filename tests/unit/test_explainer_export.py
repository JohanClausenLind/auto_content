"""Gate F6 offline: the export bundle from a fake episode, and what verify_export refuses."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from content_factory.explainer.export import (
    AI_LABEL,
    EXPORT_MANIFEST,
    EXPORTS_DIR,
    ExportManifest,
    export_bundle,
    rights_record,
    verify_export,
)
from content_factory.explainer.pipeline import LEDGER_NAME, EpisodeConfig, run
from content_factory.schemas.explainer import (
    EvidencePack,
    NarrationManifest,
    ScriptPlan,
    SponsorSegment,
)
from tests.unit.explainer_fakes import CountingStills, fake_seams, sixty_config, write_tone

EXPECTED = {
    "final.mp4",
    "captions.srt",
    "captions.vtt",
    "chapters.txt",
    "chapters.json",
    "sources.md",
    "thumbnail.png",
    "titles.json",
    "rights.json",
    "stems/narration.wav",
    "stems/music.wav",
    "stems/sfx.wav",
    "stems/master.wav",
    "manifests/pack.json",
    "manifests/script.json",
    "manifests/spec.json",
    "manifests/narration-manifest.json",
    "manifests/bundle.json",
    "manifests/ledger.json",
    "review/qc_animatic.json",
    "review/review_animatic.json",
    "review/repair-0.json",
    "review/qc_final.json",
    "review/review_final.json",
}


def _quiet(_line: str) -> None:
    return None


@pytest.fixture(scope="module")
def reviewed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A fake episode through review_final: lavfi colour video, tone narration, real mux."""
    root = tmp_path_factory.mktemp("reviewed")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CF_SERVICES_DIR", str(root / "services"))
        result = run(sixty_config(root), seams=fake_seams(), until="review_final", log=_quiet)
    assert result.status == "incomplete"
    return root


@pytest.fixture
def exported(reviewed: Path, tmp_path: Path) -> EpisodeConfig:
    shutil.copytree(reviewed, tmp_path, dirs_exist_ok=True)
    config = sixty_config(tmp_path)
    export_bundle(config.episode_dir, config=config, render_stills=CountingStills())
    return config


def _exports(config: EpisodeConfig) -> Path:
    return config.episode_dir / EXPORTS_DIR


def test_export_writes_every_file_and_hashes_it(exported: EpisodeConfig) -> None:
    folder = _exports(exported)
    manifest = ExportManifest.model_validate_json((folder / EXPORT_MANIFEST).read_text())
    assert {f.path for f in manifest.files} == EXPECTED
    written = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    assert written == EXPECTED | {EXPORT_MANIFEST}
    assert manifest.ai_disclosure and not manifest.paid_promotion


def test_a_fresh_export_verifies(exported: EpisodeConfig) -> None:
    assert verify_export(exported.episode_dir) == []


def test_chapters_start_at_zero_in_youtube_form(exported: EpisodeConfig) -> None:
    lines = (_exports(exported) / "chapters.txt").read_text().splitlines()
    assert lines[0] == "00:00 Opening"
    assert lines[-1].endswith(" What it means") and len(lines) == 7
    chapters = json.loads((_exports(exported) / "chapters.json").read_text())
    assert [c["at_s"] for c in chapters] == sorted({c["at_s"] for c in chapters})


def test_sources_and_titles_carry_claims_and_the_screenshot_note(exported: EpisodeConfig) -> None:
    sources = (_exports(exported) / "sources.md").read_text()
    assert "A screenshot shows what a source said, not that it is true." in sources
    assert "https://en.wikipedia.org/wiki/Bicycle_gearing" in sources
    assert "Accessed: 2026-09-22" in sources
    [title] = json.loads((_exports(exported) / "titles.json").read_text())
    assert title["claim_ids"] == ["clm_dist_top", "clm_torque_x4"]


def test_a_stale_pack_hash_fails_naming_the_file(exported: EpisodeConfig) -> None:
    path = exported.episode_dir / LEDGER_NAME
    ledger = json.loads(path.read_text())
    freeze = next(e for e in ledger["steps"] if e["step"] == "freeze_evidence")
    freeze["artifacts"]["pack"]["sha256"] = "a" * 64
    path.write_text(json.dumps(ledger))
    issues = verify_export(exported.episode_dir)
    assert [i.where for i in issues] == ["exports/manifests/pack.json"]
    assert issues[0].kind == "stale_binding"
    assert "freeze_evidence.pack is aaaaaaaaaaaa" in issues[0].message


def test_a_file_edited_after_export_is_stale(exported: EpisodeConfig) -> None:
    captions = _exports(exported) / "captions.srt"
    captions.write_text(captions.read_text() + "\n")
    issues = verify_export(exported.episode_dir)
    assert "exports/captions.srt" in {i.where for i in issues}


def test_a_file_nobody_exported_is_stale(exported: EpisodeConfig) -> None:
    (_exports(exported) / "notes.txt").write_text("left over")
    [issue] = verify_export(exported.episode_dir)
    assert issue.where == "exports/notes.txt" and "not in the export manifest" in issue.message


def test_a_step_rerun_after_export_makes_the_export_stale(exported: EpisodeConfig) -> None:
    path = exported.episode_dir / LEDGER_NAME
    ledger = json.loads(path.read_text())
    mix = next(e for e in ledger["steps"] if e["step"] == "mix")
    mix["fingerprint"] = "b" * 64
    path.write_text(json.dumps(ledger))
    issues = verify_export(exported.episode_dir)
    assert [i.where for i in issues] == [f"exports/{EXPORT_MANIFEST}"]
    assert "was made from mix" in issues[0].message


def test_a_stem_shorter_than_the_master_fails(exported: EpisodeConfig) -> None:
    write_tone(_exports(exported) / "stems" / "music.wav", 1000)
    issues = verify_export(exported.episode_dir)
    timing = [i for i in issues if i.kind == "timing"]
    assert [i.where for i in timing] == ["exports/stems/music.wav"]


def test_a_missing_export_says_how_to_make_one(reviewed: Path) -> None:
    [issue] = verify_export(sixty_config(reviewed).episode_dir)
    assert issue.where == f"exports/{EXPORT_MANIFEST}" and "explainer export" in issue.fix


def test_rights_flag_sponsors_generated_music_and_synthesized_speech(
    exported: EpisodeConfig,
) -> None:
    folder = _exports(exported) / "manifests"
    pack = EvidencePack.model_validate_json((folder / "pack.json").read_text())
    script = ScriptPlan.model_validate_json((folder / "script.json").read_text())
    narration = NarrationManifest.model_validate_json(
        (folder / "narration-manifest.json").read_text()
    )
    sponsor = SponsorSegment(
        segment_id="seg_sponsor001",
        sponsor_name="Acme Tools",
        spoken_text="This episode is sponsored by Acme Tools.",
        disclosure_text="Sponsored by Acme Tools",
    )
    config = exported.model_copy(update={"music_id": "explainer_light_pulse"})
    rights = rights_record(config, pack, script.model_copy(update={"sponsor": sponsor}), narration)
    assert (
        rights["paid_promotion"]["required"] and rights["paid_promotion"]["sponsor"] == "Acme Tools"
    )
    disclosure = rights["ai_disclosure"]
    assert disclosure["required"] and disclosure["label"] == AI_LABEL
    assert any("music explainer_light_pulse is generated" in r for r in disclosure["reasons"])
    assert rights["music"][0]["licence"].startswith("MiniMax-Music3 Community License")
    assert rights["narration"]["synthesized_takes"] == 7
