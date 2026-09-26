"""Gate F4 offline: the explainer lane's ledger, cache reuse, resume, stop, block and atomicity."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from content_factory.artifacts.store import FilesystemArtifactStore
from content_factory.explainer.pipeline import (
    LEDGER_NAME,
    QUEUE_NAME,
    REPO_ROOT,
    STEP_NAMES,
    WORKSPACE,
    CaptureInput,
    EpisodeConfig,
    ReviewerSettings,
    run,
    stored_tiles,
)
from content_factory.explainer.qc import QcFinding
from content_factory.explainer.tts_bench import LicenseGateError
from content_factory.runners.registry import RunStopped
from content_factory.schemas.explainer import (
    CaptureSection,
    ExplainerRenderBundle,
    ReviewReport,
    SourceCaptureManifest,
    Viewport,
)
from tests.unit.explainer_fakes import (
    SIXTY,
    CannedQc,
    CountingRender,
    fake_seams,
    sixty_config,
)


def _quiet(_line: str) -> None:
    return None


def _ledger(config: EpisodeConfig) -> dict[str, Any]:
    return json.loads((config.episode_dir / LEDGER_NAME).read_text())


def _statuses(config: EpisodeConfig) -> dict[str, str]:
    return {e["step"]: e["status"] for e in _ledger(config)["steps"]}


def _changed_spec(folder: Path) -> Path:
    spec = json.loads((SIXTY / "spec.json").read_text())
    spec["scenes"][0]["purpose"] = "Open on the one number the episode explains."
    path = folder / "spec.json"
    path.write_text(json.dumps(spec))
    return path


@pytest.fixture(scope="module")
def finished(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One full fake run shared by the tests that start from a finished episode."""
    root = tmp_path_factory.mktemp("finished")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CF_SERVICES_DIR", str(root / "services"))
        result = run(sixty_config(root), seams=fake_seams(), log=_quiet)
    assert result.status == "done" and result.ready
    return root


def _copy_of(finished: Path, folder: Path) -> EpisodeConfig:
    shutil.copytree(finished, folder, dirs_exist_ok=True)
    return sixty_config(folder)


def test_a_full_run_records_every_step_done_with_stored_artifacts(finished: Path) -> None:
    config = sixty_config(finished)
    ledger = _ledger(config)
    assert [e["step"] for e in ledger["steps"]] == list(STEP_NAMES)
    assert ledger["status"] == "done" and ledger["ready"] is True
    store = FilesystemArtifactStore(config.output_root.parent / "store")
    for entry in ledger["steps"]:
        assert entry["status"] == "done" and len(entry["fingerprint"]) == 64, entry["step"]
        assert entry["started_at"] and entry["finished_at"] and entry["error"] is None
        assert entry["artifacts"], entry["step"]
        for record in entry["artifacts"].values():
            assert store.exists(WORKSPACE, record["store_key"])
            assert record["store_key"].endswith(record["sha256"] + Path(record["path"]).suffix)


def test_a_second_run_reuses_every_step_and_renders_nothing(finished: Path, tmp_path: Path) -> None:
    config = _copy_of(finished, tmp_path)
    renders = CountingRender()
    seams = fake_seams(render_video=renders)
    first = {e["step"]: e["fingerprint"] for e in _ledger(config)["steps"]}
    again = run(config, seams=seams, log=_quiet)
    assert again.status == "done" and again.ready
    assert again.ran == () and again.skipped == STEP_NAMES
    assert renders.calls == []
    assert {e["step"]: e["fingerprint"] for e in _ledger(config)["steps"]} == first


class OneMoreCheckQc(CannedQc):
    """The canned findings plus one more passing check, as a new QC version would add."""

    def __call__(self, bundle: ExplainerRenderBundle, mp4: Path, **kw: object) -> list[QcFinding]:
        extra = QcFinding(
            "small_screen", bundle.timeline.scenes[0].scene_id, None, 0, 1.0, 0.0, True, ""
        )
        return [*super().__call__(bundle, mp4, **kw), extra]


def test_a_qc_change_reruns_qc_and_repair_but_never_the_final_render(
    finished: Path, tmp_path: Path
) -> None:
    config = _copy_of(finished, tmp_path)
    renders = CountingRender()
    result = run(config, seams=fake_seams(render_video=renders, qc=OneMoreCheckQc()), log=_quiet)
    assert result.status == "done"
    assert {"qc_animatic", "repair", "qc_final"} <= set(result.ran)
    assert "render_final" in result.skipped and renders.calls == []


def test_a_changed_spec_reruns_its_own_step_and_compile_onward_only(
    finished: Path, tmp_path: Path
) -> None:
    config = _copy_of(finished, tmp_path)
    changed = _changed_spec(tmp_path)
    renders = CountingRender()
    result = run(
        config.model_copy(update={"spec": changed}),
        seams=fake_seams(render_video=renders),
        log=_quiet,
    )
    assert result.status == "done"
    untouched = ("freeze_evidence", "lock_script", "capture_sources", "narrate", "mix")
    assert result.skipped == untouched
    assert result.ran == tuple(s for s in STEP_NAMES if s not in untouched)
    assert STEP_NAMES.index("plan_visuals") < STEP_NAMES.index("compile")
    assert len(renders.calls) == 2


def test_a_stop_between_steps_keeps_the_ledger_consistent_and_resumes_there(
    tmp_path: Path,
) -> None:
    config = sixty_config(tmp_path)
    asked: list[str] = []

    def stop_before_compile() -> str | None:
        asked.append("?")
        return "operator asked" if len(asked) > STEP_NAMES.index("compile") else None

    stopped = run(config, seams=fake_seams(), stop=stop_before_compile, log=_quiet)
    assert stopped.status == "stopped" and stopped.stopped_at == "compile"
    ledger = _ledger(config)
    assert ledger["stopped"] == {"before": "compile", "reason": "operator asked"}
    assert ledger["ready"] is False
    done = STEP_NAMES[: STEP_NAMES.index("compile")]
    assert _statuses(config) == dict.fromkeys(done, "done")
    resumed = run(config, seams=fake_seams(), log=_quiet)
    assert resumed.status == "done" and resumed.ready
    assert resumed.skipped == done
    assert resumed.ran == STEP_NAMES[len(done) :]
    assert _ledger(config)["stopped"] is None


def test_a_stop_inside_a_step_marks_it_stopped_and_the_next_run_redoes_it(
    tmp_path: Path,
) -> None:
    config = sixty_config(tmp_path)

    def sigterm_mid_render(*_args: object) -> Path:
        raise RunStopped("SIGTERM", at="render_animatic")

    stopped = run(config, seams=fake_seams(render_video=sigterm_mid_render), log=_quiet)
    assert stopped.status == "stopped" and stopped.stopped_at == "render_animatic"
    assert _statuses(config)["render_animatic"] == "stopped"
    assert _ledger(config)["stopped"] == {"during": "render_animatic", "reason": "SIGTERM"}
    resumed = run(config, seams=fake_seams(), log=_quiet)
    assert resumed.status == "done"
    assert resumed.ran[0] == "render_animatic"
    assert "compile" in resumed.skipped


def test_a_blocked_qc_ends_blocked_with_a_review_queue_and_no_ready_flag(tmp_path: Path) -> None:
    config = sixty_config(tmp_path)
    renders = CountingRender()
    result = run(config, seams=fake_seams(qc=CannedQc(fail=True), render_video=renders), log=_quiet)
    assert result.status == "blocked" and result.stopped_at == "repair"
    assert not result.ready and _ledger(config)["ready"] is False
    statuses = _statuses(config)
    assert statuses["repair"] == "blocked"
    assert "render_final" not in statuses and "export" not in statuses
    assert len(renders.calls) == 1
    queue = json.loads((config.episode_dir / QUEUE_NAME).read_text())
    assert queue["step"] == "repair"
    [item] = queue["items"]
    assert item["check"] == "overlap" and item["scene_id"] == "scn_question"
    assert item["evidence"] == "two labels overlap by 14 px"
    assert item["beat_id"] is not None and item["repair"] is None
    again = run(config, seams=fake_seams(qc=CannedQc(fail=True)), log=_quiet)
    assert again.status == "blocked" and not again.ready


def test_a_fixed_spec_clears_the_block_and_the_review_queue(tmp_path: Path) -> None:
    config = sixty_config(tmp_path)
    run(config, seams=fake_seams(qc=CannedQc(fail=True)), log=_quiet)
    fixed = _changed_spec(tmp_path)
    result = run(config.model_copy(update={"spec": fixed}), seams=fake_seams(), log=_quiet)
    assert result.status == "done" and result.ready
    assert not (config.episode_dir / QUEUE_NAME).exists()


def test_a_failed_ledger_rename_leaves_the_previous_ledger_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = sixty_config(tmp_path)
    original = Path.replace
    ledger_writes: list[Path] = []

    def replace(self: Path, target: Any) -> Path:
        if Path(target).name == LEDGER_NAME:
            ledger_writes.append(self)
            assert self.name != LEDGER_NAME, "the ledger is only ever renamed into place"
            if len(ledger_writes) == 4:
                raise OSError("disk full")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError, match="disk full"):
        run(config, seams=fake_seams(), log=_quiet)
    # Writes: start, freeze_evidence, lock_script, then the failed plan_visuals record.
    assert _statuses(config) == {"freeze_evidence": "done", "lock_script": "done"}
    monkeypatch.setattr(Path, "replace", original)
    resumed = run(config, seams=fake_seams(), log=_quiet)
    assert resumed.status == "done"
    assert resumed.skipped == ("freeze_evidence", "lock_script")


def test_a_deleted_artifact_reruns_its_step(finished: Path, tmp_path: Path) -> None:
    config = _copy_of(finished, tmp_path)
    (config.episode_dir / "animatic" / "animatic.mp4").unlink()
    renders = CountingRender()
    result = run(config, seams=fake_seams(render_video=renders), log=_quiet)
    assert result.status == "done"
    assert result.ran[0] == "render_animatic"
    assert "compile" in result.skipped


def test_an_excluded_synth_is_refused_by_the_licence_gate(tmp_path: Path) -> None:
    with pytest.raises(LicenseGateError, match="voxtral-tts is excluded"):
        sixty_config(tmp_path, synth="voxtral-tts")


def test_an_unconfigured_reviewer_leaves_an_uncertain_advisory_report(tmp_path: Path) -> None:
    config = sixty_config(tmp_path)
    run(config, seams=fake_seams(), until="review_animatic", log=_quiet)
    path = config.episode_dir / "review_animatic" / "report.json"
    report = ReviewReport.model_validate_json(path.read_text())
    assert report.disposition == "uncertain" and report.authority == "advisory"
    assert "no reviewer is configured" in report.findings[0].evidence


def test_an_unreachable_reviewer_is_named_in_the_report(tmp_path: Path) -> None:
    settings = ReviewerSettings(enabled=True, base_url="http://127.0.0.1:9/v1")
    config = sixty_config(tmp_path, reviewer=settings)
    seams = fake_seams(probe_reviewer=lambda _url: False)
    result = run(config, seams=seams, until="review_animatic", log=_quiet)
    assert result.status == "incomplete" and not result.ready
    path = config.episode_dir / "review_animatic" / "report.json"
    report = ReviewReport.model_validate_json(path.read_text())
    assert report.disposition == "uncertain"
    assert "http://127.0.0.1:9/v1 is unreachable" in report.findings[0].evidence
    assert _ledger(config)["steps"][-1]["resource"] == "gpu"


def test_a_stored_capture_whose_wacz_changed_blocks_capture_sources(tmp_path: Path) -> None:
    manifest = {
        "capture_id": "cap_000000000001",
        "source_id": "src_wiki_gear",
        "url": "https://example.org/page",
        "captured_at": "2026-09-22T00:00:00Z",
        "capture_kind": "wacz",
        "artifact_sha256": "0" * 64,
        "viewport": {"width": 1280, "height": 800},
        "page_height_px": 1600,
        "text_sha256": "1" * 64,
        "extractor": "resolve-passages",
        "extractor_version": "1",
        "sections": [{"section_id": "sec_00000001", "order": 0, "scroll_y_px": 0}],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "page.wacz").write_bytes(b"not the bytes the manifest was made from")
    capture = CaptureInput(
        manifest=tmp_path / "manifest.json",
        wacz=tmp_path / "page.wacz",
        url="https://example.org/page",
        tiles_dir=tmp_path / "tiles",
    )
    config = sixty_config(tmp_path, captures=(capture,))
    result = run(config, seams=fake_seams(), log=_quiet)
    assert result.status == "blocked" and result.stopped_at == "capture_sources"
    [item] = json.loads((config.episode_dir / QUEUE_NAME).read_text())["items"]
    assert item["check"] == "stale_binding" and "page.wacz hashes to" in item["evidence"]


def _manifest_with_page(folder: Path, page_height_px: int) -> SourceCaptureManifest:
    manifest = SourceCaptureManifest(
        capture_id="cap_000000000002",
        source_id="src_wiki_gear",
        url="https://example.org/page",
        captured_at="2026-09-22T00:00:00Z",
        capture_kind="wacz",
        artifact_sha256="0" * 64,
        viewport=Viewport(width=1280, height=800),
        page_height_px=page_height_px,
        text_sha256="1" * 64,
        extractor="resolve-passages",
        extractor_version="1",
        sections=(CaptureSection(section_id="sec_00000001", order=0, scroll_y_px=0),),
    )
    (folder / "cap_000000000002.json").write_text(manifest.canonical_json())
    return manifest


def test_stored_tiles_take_the_resolvers_recorded_offsets(tmp_path: Path) -> None:
    manifest = _manifest_with_page(tmp_path, 2000)
    index = [
        {"path": "output/x/tiles/tile-0001.png", "y_px": 800},
        {"path": "output/x/tiles/tile-0000.png", "y_px": 0},
        {"path": "output/x/tiles/tile-0002.png", "y_px": 1187},
    ]
    (tmp_path / "cap_000000000002.tiles.json").write_text(json.dumps(index))
    item = CaptureInput(
        manifest=tmp_path / "cap_000000000002.json",
        wacz=tmp_path / "page.wacz",
        url="https://example.org/page",
        tiles_dir=tmp_path / "unused",
    )
    tiles = stored_tiles(item, manifest)
    assert [t.y_px for t in tiles] == [0, 800, 1187]
    assert tiles[0].path == REPO_ROOT / "output/x/tiles/tile-0000.png"


def test_stored_tiles_without_an_index_follow_the_resolvers_scroll_rule(tmp_path: Path) -> None:
    manifest = _manifest_with_page(tmp_path, 2000)
    folder = tmp_path / "tiles"
    folder.mkdir()
    for name in ("tile-0002.png", "tile-0000.png", "tile-0001.png", "notes.txt"):
        (folder / name).write_bytes(b"")
    item = CaptureInput(
        manifest=tmp_path / "cap_000000000002.json",
        wacz=tmp_path / "page.wacz",
        url="https://example.org/page",
        tiles_dir=folder,
    )
    tiles = stored_tiles(item, manifest)
    assert [(t.path.name, t.y_px) for t in tiles] == [
        ("tile-0000.png", 0),
        ("tile-0001.png", 800),
        ("tile-0002.png", 1200),
    ]
