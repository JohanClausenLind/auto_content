"""Wrap the Blender skill's per-shot output directory as a ``ControlBundle``.

The skill writes ``<kind>/frames/NNNN.{png,exr}`` + ``NNNN.done.json`` for the rendered passes,
``skeleton/frames/NNNN.json`` and ``layout/frames/NNNN.json``, ``camera.json`` and
``metadata.json``. This module verifies every recorded digest against the bytes on disk, renders
the two derived tracks content-factory owns (``pose_skeleton`` PNGs from the skeleton JSON with the
canonical OpenPose colours, ``layout_boxes`` PNGs from the layout JSON) and builds the bundle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from content_factory.schemas.base import canonical_dumps, sha256_hex
from content_factory.schemas.sequences import Box, ControlAsset, ControlKind, Point, SkeletonPose
from content_factory.schemas.shots import (
    CameraFrame,
    ControlBundle,
    ControlEncoding,
    ControlTrack,
    ShotSpec,
    SubjectTrack,
)
from content_factory.sequences.control_compile import (
    encode_png,
    render_layout_boxes,
    render_openpose_frame,
)

BUNDLE_BUILDER_VERSION = "0.1.0"

KIND_ENCODING: dict[ControlKind, ControlEncoding] = {
    ControlKind.rough_rgb: ControlEncoding.rgb8,
    ControlKind.depth: ControlEncoding.gray8,
    ControlKind.depth16: ControlEncoding.gray16,
    ControlKind.depth_exr: ControlEncoding.exr32,
    ControlKind.normals: ControlEncoding.rgb8,
    ControlKind.segmentation: ControlEncoding.index8,
    ControlKind.canny: ControlEncoding.gray8,
    ControlKind.pose_skeleton: ControlEncoding.rgb8,
    ControlKind.layout_boxes: ControlEncoding.rgb8,
}
# Passes the skill renders to files (the other two are derived here from JSON).
FILE_KINDS: dict[ControlKind, str] = {
    ControlKind.rough_rgb: "png",
    ControlKind.depth: "png",
    ControlKind.depth16: "png",
    ControlKind.depth_exr: "exr",
    ControlKind.normals: "png",
    ControlKind.segmentation: "png",
    ControlKind.canny: "png",
}


class BundleError(RuntimeError):
    pass


def skeleton_frame_to_poses(doc: dict[str, Any]) -> dict[str, SkeletonPose]:
    """Skill skeleton JSON -> one SkeletonPose per character. Joints outside the frame are dropped
    (Point is constrained to [0, 1]); bones with a missing end go with them."""
    poses: dict[str, SkeletonPose] = {}
    for person in doc.get("people", []):
        joints = {
            name: Point(x=j["x"], y=j["y"])
            for name, j in person.get("joints", {}).items()
            if j.get("in_frame") and 0.0 <= j["x"] <= 1.0 and 0.0 <= j["y"] <= 1.0
        }
        if not joints:
            continue
        bones = tuple(
            (a, b)
            for a, b in (tuple(bone) for bone in person.get("bones", []))
            if a in joints and b in joints
        )
        poses[str(person["character_id"])] = SkeletonPose(joints=joints, bones=bones)
    return poses


def layout_frame_to_boxes(doc: dict[str, Any]) -> dict[str, Box | None]:
    out: dict[str, Box | None] = {}
    for obj in doc.get("objects", []):
        box = obj.get("box")
        if box and box["w"] > 0 and box["h"] > 0:
            out[str(obj["object_id"])] = Box(
                x=box["x"], y=box["y"], w=min(box["w"], 1.0), h=min(box["h"], 1.0)
            )
        else:
            out[str(obj["object_id"])] = None
    return out


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise BundleError(f"missing skill output {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _verified_digest(file_path: Path, marker: dict[str, Any]) -> str:
    expected = marker.get("png_sha256") or marker.get("exr_sha256") or marker.get("json_sha256")
    if not file_path.exists():
        raise BundleError(f"missing pass file {file_path}")
    actual = sha256_hex(file_path.read_bytes())
    if expected != actual:
        raise BundleError(f"sha256 mismatch for {file_path}: marker {expected} != file {actual}")
    return actual


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _derived_track(
    shot_dir: Path,
    kind: ControlKind,
    frames: list[int],
    images: dict[int, bytes],
    *,
    plan_hash: str,
    shot_id: str,
    width: int,
    height: int,
    compiler_version: str,
) -> ControlTrack:
    assets: list[ControlAsset] = []
    for frame in frames:
        png = images[frame]
        sha = sha256_hex(png)
        stem = shot_dir / kind.value / "frames" / f"{frame:04d}"
        _write(stem.with_suffix(".png"), png)
        marker = {
            "frame_index": frame,
            "pass": kind.value,
            "input_hash": sha256_hex(
                canonical_dumps(
                    {
                        "plan_hash": plan_hash,
                        "pass": kind.value,
                        "frame_index": frame,
                        "builder": compiler_version,
                    }
                ).encode()
            ),
            "png_sha256": sha,
            "width": width,
            "height": height,
        }
        _write(stem.with_suffix(".done.json"), (canonical_dumps(marker) + "\n").encode())
        assets.append(
            ControlAsset(
                kind=kind,
                frame_index=frame,
                width=width,
                height=height,
                png_sha256=sha,
                motion_plan_hash=plan_hash,
                compiler_version=compiler_version,
                compiler="blender",
                shot_id=shot_id,
            )
        )
    return ControlTrack(kind=kind, encoding=KIND_ENCODING[kind], frames=tuple(assets))


def build_control_bundle(
    shot: ShotSpec, shot_dir: Path, *, compiler_version: str = BUNDLE_BUILDER_VERSION
) -> ControlBundle:
    metadata = _load(shot_dir / "metadata.json")
    camera_doc = _load(shot_dir / "camera.json")
    if (metadata["width"], metadata["height"], metadata["frame_count"]) != (
        shot.width,
        shot.height,
        shot.frame_count,
    ):
        rendered = f"{metadata['width']}x{metadata['height']}x{metadata['frame_count']}"
        raise BundleError(
            f"{shot.shot_id}: skill rendered {rendered} but the spec says"
            f" {shot.width}x{shot.height}x{shot.frame_count}"
        )
    frames: list[int] = [int(f) for f in metadata["frames_rendered"]]
    plan_hash = shot.content_hash()
    w, h = shot.width, shot.height
    passes = list(shot.render.passes)

    tracks: list[ControlTrack] = []
    for kind in passes:
        ext = FILE_KINDS.get(kind)
        if ext is None:
            continue
        assets: list[ControlAsset] = []
        for frame in frames:
            stem = shot_dir / kind.value / "frames" / f"{frame:04d}"
            marker = _load(stem.with_suffix(".done.json"))
            sha = _verified_digest(stem.with_suffix(f".{ext}"), marker)
            assets.append(
                ControlAsset(
                    kind=kind,
                    frame_index=frame,
                    width=w,
                    height=h,
                    png_sha256=sha,
                    motion_plan_hash=plan_hash,
                    compiler_version=compiler_version,
                    compiler="blender",
                    shot_id=shot.shot_id,
                )
            )
        tracks.append(ControlTrack(kind=kind, encoding=KIND_ENCODING[kind], frames=tuple(assets)))

    entity_ids = [c.id for c in shot.characters] + [p.id for p in shot.props]
    layouts_by_frame: dict[int, dict[str, Box | None]] = {}
    poses_by_frame: dict[int, dict[str, SkeletonPose]] = {}
    if ControlKind.layout_boxes in passes:
        for frame in frames:
            stem = shot_dir / "layout" / "frames" / f"{frame:04d}"
            _verified_digest(stem.with_suffix(".json"), _load(stem.with_suffix(".done.json")))
            layouts_by_frame[frame] = layout_frame_to_boxes(_load(stem.with_suffix(".json")))
    if ControlKind.pose_skeleton in passes:
        for frame in frames:
            stem = shot_dir / "skeleton" / "frames" / f"{frame:04d}"
            _verified_digest(stem.with_suffix(".json"), _load(stem.with_suffix(".done.json")))
            poses_by_frame[frame] = skeleton_frame_to_poses(_load(stem.with_suffix(".json")))

    if ControlKind.pose_skeleton in passes:
        images = {
            f: encode_png(render_openpose_frame(poses_by_frame[f].values(), w, h)) for f in frames
        }
        tracks.append(
            _derived_track(
                shot_dir,
                ControlKind.pose_skeleton,
                frames,
                images,
                plan_hash=plan_hash,
                shot_id=shot.shot_id,
                width=w,
                height=h,
                compiler_version=compiler_version,
            )
        )
    if ControlKind.layout_boxes in passes:
        images = {
            f: encode_png(
                render_layout_boxes([layouts_by_frame[f].get(e) for e in entity_ids], w, h)
            )
            for f in frames
        }
        tracks.append(
            _derived_track(
                shot_dir,
                ControlKind.layout_boxes,
                frames,
                images,
                plan_hash=plan_hash,
                shot_id=shot.shot_id,
                width=w,
                height=h,
                compiler_version=compiler_version,
            )
        )

    seg_objects: dict[str, int] = {
        str(k): int(v) for k, v in metadata.get("seg", {}).get("objects", {}).items()
    }
    subjects: list[SubjectTrack] = []
    for entity_id in entity_ids:
        seg_id = seg_objects.get(entity_id, 0)
        if seg_id < 1:
            continue  # SubjectTrack needs a segmentation index; un-segmented props have none
        layouts = (
            tuple(
                layouts_by_frame.get(f, {}).get(entity_id) if f in layouts_by_frame else None
                for f in range(shot.frame_count)
            )
            if layouts_by_frame
            else ()
        )
        poses = (
            tuple(
                poses_by_frame.get(f, {}).get(entity_id) if f in poses_by_frame else None
                for f in range(shot.frame_count)
            )
            if poses_by_frame
            else ()
        )
        subjects.append(
            SubjectTrack(
                subject_id=entity_id,
                label=entity_id,
                segmentation_index=seg_id,
                layouts=layouts,
                poses=poses,
            )
        )

    camera = tuple(
        CameraFrame(
            frame_index=int(c["frame_index"]),
            position=tuple(c["position"]),
            rotation_quat_wxyz=tuple(c["rotation_quat_wxyz"]),
            lens_mm=float(c["lens_mm"]),
            sensor_width_mm=float(c["sensor_width_mm"]),
            look_at=tuple(c["look_at"]) if c.get("look_at") is not None else None,
            intrinsics=tuple(c["intrinsics"]),
            world_to_camera=tuple(c["world_to_camera"]),
        )
        for c in camera_doc.get("frames", [])
    )

    bundle = ControlBundle(
        bundle_id=f"cbd_{plan_hash[:12]}",
        shot_id=shot.shot_id,
        plan_hash=plan_hash,
        compiler="blender",
        compiler_version=compiler_version,
        blender_version=str(metadata.get("blender_version") or "")[:64] or None,
        width=w,
        height=h,
        frame_count=shot.frame_count,
        fps=shot.fps,
        anchor_frames=shot.anchor_frames,
        tracks=tuple(tracks),
        subjects=tuple(subjects),
        camera=camera,
    )
    _write(
        shot_dir / "bundle.json",
        (json.dumps(bundle.model_dump(mode="json"), indent=1, sort_keys=True) + "\n").encode(),
    )
    return bundle
