"""A cached clip is only reused when the graph and the bytes both still match."""

from __future__ import annotations

import json
from pathlib import Path

from content_factory.media.ltx_packages import ltx_i2v_guided_package, ltx_i2v_package
from content_factory.media.video_generate import ComfyUIVideoBackend, MockVideoBackend
from content_factory.workflows.stages import _cached_output, _write_atomic


def _backend(**kwargs) -> ComfyUIVideoBackend:
    return ComfyUIVideoBackend(
        "http://127.0.0.1:8188",
        ltx_i2v_package(**kwargs),
        guided_packages={1: ltx_i2v_guided_package(1, **kwargs)},
    )


def test_the_graph_digest_changes_when_a_node_does() -> None:
    """The assertion the bug needed: editing a node must not leave the key alone."""
    base = _backend().graph_fingerprint(0)
    assert base == _backend().graph_fingerprint(0)  # deterministic
    for node, field, value in (
        ("11", "sampler_name", "euler"),
        ("10", "cfg", 3.0),
        ("12", "sigmas", "1.0, 0.5, 0.0"),
        ("14", "tile_size", 256),
    ):
        package = ltx_i2v_package()
        workflow = {
            key: ({**spec, "inputs": {**spec["inputs"], field: value}} if key == node else spec)
            for key, spec in package.api_workflow.items()
        }
        edited = ComfyUIVideoBackend(
            "http://127.0.0.1:8188", package.model_copy(update={"api_workflow": workflow})
        )
        assert edited.graph_fingerprint(0) != base, f"{node}.{field}"


def test_a_bound_parameter_does_not_move_the_graph_digest() -> None:
    """The other half of the design."""
    assert _backend().graph_fingerprint(0) == _backend(width=512).graph_fingerprint(0)
    # `length` is the exception, and deliberately so: it is a bound parameter *and* a declared
    # default on LTXVImgToVideo, because the package is built for a fixed clip length.
    assert _backend().graph_fingerprint(0) != _backend(length=25).graph_fingerprint(0)


def test_a_guided_graph_is_a_different_graph() -> None:
    base = _backend()
    plain, guided = base.graph_fingerprint(0), base.graph_fingerprint(1)
    assert plain["package_id"] != guided["package_id"]
    assert plain["graph_sha256"] != guided["graph_sha256"]


def test_the_digest_covers_the_package_version_and_id() -> None:
    fingerprint = _backend().graph_fingerprint(0)
    assert fingerprint["backend"] == "comfyui"
    assert fingerprint["package_id"] == "ltx-2.5.i2v"
    assert fingerprint["package_version"]
    assert len(fingerprint["graph_sha256"]) == 32


def test_a_backend_with_no_graph_still_has_a_fingerprint() -> None:
    """The mock has no package. It must not need one — every lane runs on it offline."""
    assert MockVideoBackend().graph_fingerprint() == {"backend": "mock"}


def test_a_cache_hit_needs_the_marker_the_file_and_the_bytes(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    marker = tmp_path / ".done.json"
    sha = _write_atomic(clip, b"the clip")
    marker.write_text(json.dumps({"input_hash": "h1", "video_sha256": sha}))

    assert _cached_output(marker, clip, "h1", "video_sha256") is not None
    # A different input hash is a different job.
    assert _cached_output(marker, clip, "h2", "video_sha256") is None
    # The bytes changed under the marker: truncated, re-copied, or edited by hand.
    clip.write_bytes(b"the clip, but shorter")
    assert _cached_output(marker, clip, "h1", "video_sha256") is None


def test_a_missing_file_or_marker_is_not_a_hit(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    marker = tmp_path / ".done.json"
    assert _cached_output(marker, clip, "h1", "video_sha256") is None
    _write_atomic(clip, b"x")
    assert _cached_output(marker, clip, "h1", "video_sha256") is None
    marker.write_text(json.dumps({"input_hash": "h1"}))
    clip.unlink()
    assert _cached_output(marker, clip, "h1", "video_sha256") is None


def test_a_marker_with_no_recorded_sha_still_hits(tmp_path: Path) -> None:
    """Markers written before the sha was recorded must not invalidate every cached clip."""
    clip = tmp_path / "clip.mp4"
    marker = tmp_path / ".done.json"
    _write_atomic(clip, b"older clip")
    marker.write_text(json.dumps({"input_hash": "h1"}))
    assert _cached_output(marker, clip, "h1", "video_sha256") is not None


def test_an_unreadable_marker_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    marker = tmp_path / ".done.json"
    _write_atomic(clip, b"x")
    marker.write_text("{half written")
    assert _cached_output(marker, clip, "h1", "video_sha256") is None


def test_an_atomic_write_leaves_no_temp_file_and_returns_the_sha(tmp_path: Path) -> None:
    """A file written straight to its final name is truncated but present if the process dies."""
    target = tmp_path / "deep" / "clip.mp4"
    sha = _write_atomic(target, b"bytes")
    assert target.read_bytes() == b"bytes"
    assert len(sha) == 64
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["clip.mp4"]


def test_writing_twice_replaces_rather_than_appends(tmp_path: Path) -> None:
    target = tmp_path / "clip.mp4"
    _write_atomic(target, b"first")
    second = _write_atomic(target, b"second")
    assert target.read_bytes() == b"second"
    assert _cached_output(_marker(tmp_path, "h", second), target, "h", "video_sha256") is not None


def _marker(directory: Path, input_hash: str, sha: str) -> Path:
    path = directory / ".done.json"
    path.write_text(json.dumps({"input_hash": input_hash, "video_sha256": sha}))
    return path
