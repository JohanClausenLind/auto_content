"""Spreading independent GPU items over more than one model server.

The gate here is not "it went faster" -- a unit test cannot prove that -- but the three properties
that make going faster safe: a pool of one behaves exactly like the serial loop it replaced, two
workers really do run at the same time, and the pictures a pooled run produces are byte-identical
to the ones the serial run produced.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from content_factory.schemas.sequences import (
    Box,
    GenerationLock,
    MotionPlan,
    SubjectKeyframe,
    TrackedSubject,
)
from content_factory.sequences.engine import MockReferenceEditBackend, build_sequence
from content_factory.services.gpu_pool import WorkerPool

LOCK = GenerationLock(
    workflow_package_id="fixture.reference-edit",
    workflow_package_version="0.1.0",
    model_revision="mock-1",
    width=256,
    height=144,
    seed=7,
    sampler="euler",
    steps=8,
    guidance=2.5,
    style_prompt="flat editorial illustration",
    camera_prompt="fixed camera",
    lighting_prompt="soft daylight",
    background_prompt="plain warm paper",
    reference_asset_sha256="0" * 64,
)


def six_keyframe_plan() -> MotionPlan:
    kfs = tuple(
        SubjectKeyframe(frame_index=i, layout=Box(x=0.1 + 0.12 * i, y=0.45, w=0.16, h=0.25))
        for i in range(6)
    )
    return MotionPlan(
        plan_id="mp_flipbook0001",
        sequence_id="seq_flipbook001",
        frame_count=6,
        canvas_width=256,
        canvas_height=144,
        subjects=(
            TrackedSubject(subject_id="subj_char00001", label="the character", keyframes=kfs),
        ),
    )


class _NamedMock(MockReferenceEditBackend):
    """A mock that carries an endpoint, the way a real backend does, so provenance is checkable."""

    def __init__(self, endpoint: str) -> None:
        super().__init__()
        self.endpoint = endpoint


class _PairedMock(_NamedMock):
    """A mock that refuses to finish a frame alone.

    Every call waits on a two-party barrier, so the frame can only complete while another worker is
    inside its own call. Against a serial loop the first wait times out and the test fails -- which
    is the point: this is the assertion that the fan-out is real and not just plumbed.
    """

    def __init__(self, endpoint: str, barrier: threading.Barrier) -> None:
        super().__init__(endpoint)
        self.barrier = barrier

    def edit(self, *args: object, **kwargs: object) -> bytes:
        self.barrier.wait()
        return super().edit(*args, **kwargs)  # type: ignore[arg-type]


def test_pool_of_one_runs_on_the_calling_thread() -> None:
    """A single endpoint must not silently acquire a thread hop: same thread, same order."""
    seen: list[int] = []

    def _work(worker: str, item: int) -> int:
        seen.append(threading.get_ident())
        return item * 2

    out = WorkerPool(["only"]).map_ordered([1, 2, 3], _work)
    assert out == [2, 4, 6]
    assert set(seen) == {threading.get_ident()}


def test_map_ordered_returns_results_in_item_order_not_completion_order() -> None:
    """Later items finish first; the caller still gets them in the order it asked for."""

    def _work(worker: str, item: int) -> int:
        if item == 0:  # the first item is the slowest, so completion order is reversed
            threading.Event().wait(0.05)
        return item

    assert WorkerPool(["a", "b", "c"]).map_ordered([0, 1, 2], _work) == [0, 1, 2]


def test_first_failure_in_item_order_propagates() -> None:
    def _work(worker: str, item: int) -> int:
        if item == 1:
            msg = "endpoint refused the job"
            raise RuntimeError(msg)
        return item

    with pytest.raises(RuntimeError, match="endpoint refused"):
        WorkerPool(["a", "b"]).map_ordered([0, 1, 2], _work)


def test_empty_pool_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one worker"):
        WorkerPool([])


def test_two_hosts_generate_frames_at_the_same_time(tmp_path: Path) -> None:
    """The barrier makes this fail (by timeout) on any implementation that is secretly serial."""
    barrier = threading.Barrier(2, timeout=10)
    a = _PairedMock("http://host-a:8801", barrier)
    b = _PairedMock("http://host-b:8801", barrier)

    result = build_sequence(six_keyframe_plan(), LOCK, a, tmp_path / "pooled", backends=[a, b])

    assert not result.failed
    assert len(result.frames) == 6
    assert {f["served_by"] for f in result.frames} == {
        "http://host-a:8801",
        "http://host-b:8801",
    }


def test_pooled_frames_are_byte_identical_to_serial_frames(tmp_path: Path) -> None:
    """Which card drew a frame must not change the frame. Same shas, same order, same drift."""
    plan = six_keyframe_plan()
    serial = build_sequence(plan, LOCK, MockReferenceEditBackend(), tmp_path / "serial")
    a = _NamedMock("http://host-a:8801")
    b = _NamedMock("http://host-b:8801")
    pooled = build_sequence(plan, LOCK, a, tmp_path / "pooled", backends=[a, b])

    assert pooled.anchor_sha256 == serial.anchor_sha256
    assert [f["frame_index"] for f in pooled.frames] == [f["frame_index"] for f in serial.frames]
    assert [f["png_sha256"] for f in pooled.frames] == [f["png_sha256"] for f in serial.frames]
    assert [f["input_hash"] for f in pooled.frames] == [f["input_hash"] for f in serial.frames]
    assert pooled.regenerated == serial.regenerated
    assert pooled.failed == serial.failed
    for idx in range(6):
        left = (tmp_path / "serial" / "frames" / f"{idx:04d}.png").read_bytes()
        right = (tmp_path / "pooled" / "frames" / f"{idx:04d}.png").read_bytes()
        assert left == right


def test_serving_host_is_recorded_on_disk_for_the_run_to_report(tmp_path: Path) -> None:
    a = _NamedMock("http://host-a:8801")
    b = _NamedMock("http://host-b:8801")
    build_sequence(six_keyframe_plan(), LOCK, a, tmp_path / "seq", backends=[a, b])

    served = {
        json.loads(p.read_text())["served_by"]
        for p in (tmp_path / "seq" / "frames").glob("*.done.json")
    }
    assert served <= {"http://host-a:8801", "http://host-b:8801"}
    assert served


def test_cached_frames_are_not_redispatched(tmp_path: Path) -> None:
    """A resumed run must spend GPU time only on what is missing, pool or no pool."""
    plan = six_keyframe_plan()
    workdir = tmp_path / "seq"
    first = _NamedMock("http://host-a:8801")
    build_sequence(plan, LOCK, first, workdir, backends=[first])

    second = _NamedMock("http://host-b:8801")
    again = build_sequence(plan, LOCK, second, workdir, backends=[second])

    assert second.calls == []
    assert all(f["cache_hit"] for f in again.frames)
