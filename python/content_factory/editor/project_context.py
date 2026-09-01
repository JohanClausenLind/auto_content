"""Build the Revision Box artifact context from a project directory (phase 7)."""

from __future__ import annotations

import json
from pathlib import Path

from content_factory.editor.critique import ArtifactContext, ArtifactUnit, UnitKind


class ProjectContextError(Exception):
    pass


def load_context(project_dir: Path) -> ArtifactContext:
    if not (project_dir / "manifest.json").exists():
        raise ProjectContextError(f"no project at {project_dir}")
    manifest = json.loads((project_dir / "manifest.json").read_text())
    units: list[ArtifactUnit] = []
    plan_path = project_dir / "story" / "plan.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text())
        beats = {b["beat_id"]: b for b in plan.get("beats", [])}
        for i, scene in enumerate(plan.get("scenes", [])):
            kind = (
                UnitKind.chart_scene
                if scene.get("kind") in {"chart", "big_number"}
                else UnitKind.scene
            )
            beat = beats.get(scene.get("beat_id"), {})
            dur = beat.get("planned_duration_ms")
            frames = None
            if dur:
                frames = (dur * plan.get("fps", 30) + 999) // 1000
            title = scene.get("title", {}) or {}
            label = title.get("text") if isinstance(title, dict) else None
            units.append(
                ArtifactUnit(
                    unit_id=scene["scene_id"],
                    kind=kind,
                    label=label or f"{scene.get('kind', 'scene')} scene {i + 1}",
                    ordinal=i,
                    duration_frames=frames,
                    claim_linked=bool(beat.get("claim_ids")),
                )
            )
    for ddir in (
        sorted((project_dir / "deliverables").glob("*"))
        if (project_dir / "deliverables").exists()
        else []
    ):
        copy_path = ddir / "copy.json"
        if not copy_path.exists():
            continue
        copy = json.loads(copy_path.read_text())
        if "cards" in copy:
            for i, card in enumerate(copy["cards"]):
                units.append(
                    ArtifactUnit(
                        unit_id=card["card_id"],
                        kind=UnitKind.carousel_card,
                        label=f"card {i + 1}",
                        ordinal=i,
                    )
                )
        elif "caption" in copy:
            units.append(
                ArtifactUnit(
                    unit_id=f"txt_{ddir.name[-12:]}",
                    kind=UnitKind.text,
                    label=f"{ddir.name} caption",
                    ordinal=0,
                )
            )
    if not units:
        raise ProjectContextError("project has no addressable units yet")
    return ArtifactContext(
        artifact_id=manifest["project_id"], revision_hash="0" * 64, units=tuple(units)
    )


def deliverable_of_card(project_dir: Path, card_id: str) -> str | None:
    for ddir in sorted((project_dir / "deliverables").glob("*")):
        copy_path = ddir / "copy.json"
        if copy_path.exists():
            copy = json.loads(copy_path.read_text())
            if any(c["card_id"] == card_id for c in copy.get("cards", [])):
                return ddir.name
    return None
