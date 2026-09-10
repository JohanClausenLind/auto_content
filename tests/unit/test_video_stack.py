from __future__ import annotations

import os
from pathlib import Path

import pytest

from content_factory.models.video_stack import (
    VIDEO_STACK,
    StackStatus,
    StackTier,
    verify_video_stack,
)


def _fake_file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    os.truncate(path, size)  # sparse: apparent size without writing gigabytes


def _root(tmp_path: Path) -> Path:
    (tmp_path / "models").mkdir(exist_ok=True)
    (tmp_path / "external").mkdir(exist_ok=True)
    return tmp_path


def test_catalog_covers_every_tier_with_unique_keys() -> None:
    keys = [e.key for e in VIDEO_STACK]
    assert len(keys) == len(set(keys))
    assert {e.tier for e in VIDEO_STACK} == set(StackTier)
    for e in VIDEO_STACK:
        assert e.weight_globs or e.abs_globs or e.repo_dir or e.tool, e.key
        for pattern, min_bytes in e.weight_globs:
            assert not pattern.startswith("/") and min_bytes > 0


def test_ready_when_all_weights_present_at_plausible_sizes(tmp_path: Path) -> None:
    root = _root(tmp_path)
    ltx = next(e for e in VIDEO_STACK if e.key == "ltx-2.5")
    for pattern, min_bytes in ltx.weight_globs:
        _fake_file(root / "models" / pattern.replace("*", "x"), min_bytes + 1)
    (root / "external" / "LTX-2").mkdir()
    report = verify_video_stack(root, which=lambda _t: None)
    by_key = {r.entry.key: r for r in report.entries}
    assert by_key["ltx-2.5"].status == StackStatus.ready


def test_undersized_files_do_not_count_as_ready(tmp_path: Path) -> None:
    root = _root(tmp_path)
    seed = next(e for e in VIDEO_STACK if e.key == "seedvr2")
    for pattern, _min in seed.weight_globs:
        _fake_file(root / "models" / pattern.replace("*", "x"), 1024)  # truncated stubs
    report = verify_video_stack(root, which=lambda _t: None)
    status = next(r.status for r in report.entries if r.entry.key == "seedvr2")
    assert status == StackStatus.not_downloaded


def test_incomplete_markers_read_as_downloading(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _fake_file(root / "models" / "seedvr2" / ".cache" / "x.incomplete", 10)
    report = verify_video_stack(root, which=lambda _t: None)
    status = next(r.status for r in report.entries if r.entry.key == "seedvr2")
    assert status == StackStatus.downloading


def test_gate_and_repo_only_states(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "models" / "sam3.1").mkdir(parents=True)  # LICENSE/README only, no checkpoints
    (root / "external" / "GIMM-VFI").mkdir(parents=True)
    report = verify_video_stack(root, which=lambda _t: None)
    by_key = {r.entry.key: r for r in report.entries}
    assert by_key["sam-3.1"].status == StackStatus.gated_pending
    assert by_key["gimm-vfi"].status == StackStatus.missing_weights
    assert by_key["wan-2.2-t2v"].status == StackStatus.not_downloaded


def test_verified_verdicts_survive_regardless_of_downloads(tmp_path: Path) -> None:
    root = _root(tmp_path)
    report = verify_video_stack(root, which=lambda _t: None)
    by_key = {r.entry.key: r for r in report.entries}
    assert by_key["ltx-2.3-ic-loras"].status == StackStatus.incompatible
    assert by_key["joyai-video-edit"].status == StackStatus.wont_fit_24gb
    assert by_key["rife"].status == StackStatus.manual_step
    assert by_key["ltx-2.3-ic-loras"].entry.recommendation  # a replacement is named


def test_manual_step_clears_once_the_operator_has_done_it(tmp_path: Path) -> None:
    root = _root(tmp_path)
    _fake_file(root / "external" / "Practical-RIFE" / "train_log" / "flownet.pkl", 2_000_000)
    report = verify_video_stack(root, which=lambda _t: None)
    assert next(r.status for r in report.entries if r.entry.key == "rife") == StackStatus.ready


def test_tools_and_code_only_entries(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "external" / "whisperX").mkdir(parents=True)
    found = {"ffmpeg": "/usr/bin/ffmpeg", "blender": "/snap/bin/blender"}
    report = verify_video_stack(root, which=lambda t: found.get(t))
    by_key = {r.entry.key: r for r in report.entries}
    assert by_key["ffmpeg"].status == StackStatus.ready
    assert by_key["blender"].status == StackStatus.ready
    # abs_globs entry: ready where the assets were built, not_downloaded elsewhere
    assert by_key["blender-characters"].status in {StackStatus.ready, StackStatus.not_downloaded}
    assert by_key["blender-characters"].entry.abs_globs
    assert by_key["pyscenedetect"].status == StackStatus.tool_missing
    assert by_key["whisperx"].status == StackStatus.ready  # code repo; models fetch on first use


def test_report_summary_counts(tmp_path: Path) -> None:
    report = verify_video_stack(_root(tmp_path), which=lambda _t: None)
    summary = report.summary()
    assert sum(summary.values()) == len(VIDEO_STACK)
    assert summary.get("incompatible") == 1 and summary.get("wont_fit_24gb") == 1


def test_models_root_override_and_resolution(tmp_path: Path) -> None:
    """Weights moved out of <root>/models (2026-09-05): an explicit store is honoured and the
    resolver falls back to a configured inventory root when <root>/models is gone."""
    from content_factory.models.video_stack import resolve_models_root

    root = tmp_path / "stack"
    (root / "external").mkdir(parents=True)
    store = tmp_path / "fast" / "models"
    ltx = next(e for e in VIDEO_STACK if e.key == "ltx-2.5")
    for pattern, min_bytes in ltx.weight_globs:
        _fake_file(store / pattern.replace("*", "x"), min_bytes + 1)

    # no override -> resolver picks the configured store, even when <root>/models exists
    # (on the real layout it is only the symlink index)
    assert resolve_models_root(root, configured_roots=("/nonexistent", str(store))) == store
    (root / "models").mkdir()
    assert resolve_models_root(root, configured_roots=(str(store),)) == store
    assert resolve_models_root(root, explicit=str(store)) == store
    assert resolve_models_root(root) == root / "models"

    without = verify_video_stack(root)
    assert next(r for r in without.entries if r.entry.key == "ltx-2.5").status == (
        StackStatus.not_downloaded
    )
    with_store = verify_video_stack(root, models_root=store)
    assert next(r for r in with_store.entries if r.entry.key == "ltx-2.5").status == (
        StackStatus.ready
    )
    assert with_store.models_root == str(store)


def test_default_stack_root_env_then_repo_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The stack is laid out inside the repo since 2026-09-05 (external/, models/, output/);
    $AI_VIDEO_ROOT still overrides."""
    from content_factory.models.video_stack import REPO_ROOT, default_stack_root

    assert (REPO_ROOT / "pyproject.toml").is_file()  # parents[3] really is the repo root
    monkeypatch.delenv("AI_VIDEO_ROOT", raising=False)
    assert default_stack_root() == REPO_ROOT
    monkeypatch.setenv("AI_VIDEO_ROOT", str(tmp_path / "elsewhere"))
    assert default_stack_root() == tmp_path / "elsewhere"


def test_symlink_index_resolves_to_the_store_it_points_into(tmp_path: Path) -> None:
    """<repo>/models/<category>/<Name> -> <store>/<short>: the verifier must scan the store."""
    from content_factory.models.video_stack import index_store_root, resolve_models_root

    store = tmp_path / "store"
    (store / "ltx25" / "vae").mkdir(parents=True)
    (store / "hidream-o1").mkdir()
    index = tmp_path / "repo" / "models"
    (index / "video_generation").mkdir(parents=True)
    (index / "image_generation").mkdir()
    (index / "video_generation" / "LTX-2.5").symlink_to(store / "ltx25")
    (index / "image_generation" / "HiDream-O1-Image-Dev").symlink_to(store / "hidream-o1")
    assert index_store_root(index) == store
    assert resolve_models_root(tmp_path / "repo") == store
    # explicit and configured roots still win; a plain weights dir is left alone
    assert resolve_models_root(tmp_path / "repo", explicit=str(store / "ltx25")) == store / "ltx25"
    plain = tmp_path / "plain" / "models"
    (plain / "ltx25").mkdir(parents=True)
    assert index_store_root(plain) is None
    assert resolve_models_root(tmp_path / "plain") == plain


def test_a_clip_is_never_asked_for_at_a_size_the_package_refuses() -> None:
    """The graph owns the limit and nothing upstream had asked it.

    Measured 2026-09-10: `image-to-video` on a 2560x1440 photograph and `silent-video` on a
    1920x1080 story both died at `generate_video` with "parameter 'width' above maximum 1344.0",
    raised inside the ComfyUI package validator — after the anchors had been generated.
    """
    import pytest

    from content_factory.media.video_generate import ComfyUIVideoBackend, MockVideoBackend
    from content_factory.schemas.fixtures import sample_ltx_i2v_package
    from content_factory.workflows.stages import _backend_size_cap, _clip_size

    ltx = ComfyUIVideoBackend("http://127.0.0.1:8188", sample_ltx_i2v_package())
    assert _backend_size_cap(ltx) == (1344, 1344)
    # A backend that declares no limit is left alone, mocks included.
    assert _backend_size_cap(MockVideoBackend()) is None
    assert _clip_size(MockVideoBackend(), 2560, 1440, fit=True, what="still") == (2560, 1440)

    # A supplied still has an incidental size: fit it, keep the aspect, stay on the 16 grid.
    w, h = _clip_size(ltx, 2560, 1440, fit=True, what="the supplied first frame")
    assert w <= 1344 and h <= 1344
    assert w % 16 == 0 and h % 16 == 0
    assert abs((w / h) - (2560 / 1440)) < 0.05
    # Already inside the cap: untouched.
    assert _clip_size(ltx, 1024, 576, fit=True, what="still") == (1024, 576)

    # A shot plan's size is deliberate, so it is refused rather than silently shrunk.
    with pytest.raises(RuntimeError, match="generates at most 1344x1344"):
        _clip_size(ltx, 1920, 1080, fit=False, what="shot s1")
