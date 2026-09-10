"""A finished film lands somewhere a person can find it.

A deliverable lives at `deliverables/<id>/exports/final.mp4` — correct, addressable, and no use to
anybody browsing. Measured 2026-09-10: 213 run directories held 34 films between them and the only
way to watch one was to know the path. The operator asked for the finished videos to be stored
somewhere logical, so the last stage of a run publishes the film into one flat directory named by
the run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from content_factory.workflows.stages import StageContext


def _packaged(project: Path, *, film: bytes | None = b"\x00" * 32) -> StageContext:
    """A run directory with just enough on disk for the packaging stage to have work."""
    from content_factory.runners.local import make_context

    ctx = make_context(project_dir=project, brief={"topic": "a pine cone"})
    exports = ctx.ddir() / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    if film is not None:
        (exports / "final.mp4").write_bytes(film)
    qc = ctx.ddir() / "qc"
    qc.mkdir(parents=True, exist_ok=True)
    (qc / "report.json").write_text(json.dumps({"passed": True}))
    return ctx


def test_the_film_is_hard_linked_into_the_gallery(tmp_path, monkeypatch) -> None:
    from content_factory.workflows import stages

    repo = tmp_path / "repo"
    project = repo / "output" / "overnight" / "ps1c-pinecone"
    project.mkdir(parents=True)
    monkeypatch.setattr(stages, "REPO_ROOT", repo)
    ctx = _packaged(project)
    out = stages.stage_compile_destination_packages(ctx)

    dest = repo / "videos" / "ps1c-pinecone.mp4"
    assert out.facts["gallery"] == "videos/ps1c-pinecone.mp4"
    assert dest.is_file()
    source = ctx.ddir() / "exports" / "final.mp4"
    # A hard link, not a copy: one inode, so the gallery costs nothing and deleting it is safe.
    assert dest.stat().st_ino == source.stat().st_ino
    assert dest.stat().st_nlink >= 2


def test_a_rerun_replaces_the_link_rather_than_failing(tmp_path, monkeypatch) -> None:
    from content_factory.workflows import stages

    repo = tmp_path / "repo"
    project = repo / "output" / "overnight" / "again"
    project.mkdir(parents=True)
    monkeypatch.setattr(stages, "REPO_ROOT", repo)
    ctx = _packaged(project, film=b"first")
    stages.stage_compile_destination_packages(ctx)
    (ctx.ddir() / "exports" / "final.mp4").write_bytes(b"second-cut")
    stages.stage_compile_destination_packages(ctx)
    assert (repo / "videos" / "again.mp4").read_bytes() == b"second-cut"


def test_a_run_outside_the_checkout_is_not_published(tmp_path, monkeypatch) -> None:
    """Otherwise every test that happens to package something litters the repo."""
    from content_factory.workflows import stages

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(stages, "REPO_ROOT", repo)
    ctx = _packaged(tmp_path / "elsewhere" / "run")
    out = stages.stage_compile_destination_packages(ctx)
    assert "gallery" not in out.facts
    assert not (repo / "videos").exists()


def test_an_empty_setting_turns_it_off(tmp_path, monkeypatch) -> None:
    from content_factory.config import settings as settings_mod
    from content_factory.workflows import stages

    cfg = settings_mod.get_settings()
    off = cfg.model_copy(update={"gallery": cfg.gallery.model_copy(update={"dir": ""})})
    monkeypatch.setattr(stages, "get_settings", lambda: off)
    repo = tmp_path / "repo"
    project = repo / "output" / "off"
    project.mkdir(parents=True)
    monkeypatch.setattr(stages, "REPO_ROOT", repo)
    out = stages.stage_compile_destination_packages(_packaged(project))
    assert "gallery" not in out.facts


def test_a_lane_with_no_film_publishes_nothing(tmp_path, monkeypatch) -> None:
    """`single-image` ships a picture. There is no video to put in a video gallery."""
    from content_factory.runners.local import make_context
    from content_factory.workflows import stages

    repo = tmp_path / "repo"
    project = repo / "output" / "stills"
    project.mkdir(parents=True)
    monkeypatch.setattr(stages, "REPO_ROOT", repo)
    ctx = make_context(project_dir=project, brief={"topic": "a still"})
    anchors = ctx.ddir() / "anchors"
    anchors.mkdir(parents=True, exist_ok=True)
    (anchors / "anchor.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    out = stages.stage_compile_destination_packages(ctx)
    assert "gallery" not in out.facts
    assert not (repo / "videos").exists()


def test_it_refuses_to_package_nothing(tmp_path, monkeypatch) -> None:
    """Unchanged behaviour, asserted here because the gallery runs after this check."""
    from content_factory.runners.local import make_context
    from content_factory.workflows import stages

    repo = tmp_path / "repo"
    monkeypatch.setattr(stages, "REPO_ROOT", repo)
    ctx = make_context(project_dir=repo / "output" / "empty", brief={"topic": "nothing"})
    with pytest.raises(RuntimeError, match="found nothing to ship"):
        stages.stage_compile_destination_packages(ctx)
