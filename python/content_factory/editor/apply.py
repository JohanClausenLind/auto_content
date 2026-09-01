"""Apply a FixPlan to a project as a versioned edit overlay; undo restores the prior revision.

The overlay is the deterministic input surface the workflow stages read; applying a plan writes a
new overlay revision under edits/history/ (append-only) plus edits/overlay.json (current). The
next run of the same project rebuilds exactly the dependency closure of the changed units.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from content_factory.editor.project_context import deliverable_of_card
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.editing import FixPlan


class ApplyError(Exception):
    pass


@dataclass(frozen=True)
class AppliedRevision:
    revision: int
    overlay_sha256: str
    affected_unit_ids: tuple[str, ...]


def _read_overlay(project_dir: Path) -> dict:
    path = project_dir / "edits" / "overlay.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _write_revision(
    project_dir: Path, overlay: dict, plan: FixPlan | None, *, undo_of: int | None = None
) -> AppliedRevision:
    edits = project_dir / "edits"
    history = edits / "history"
    history.mkdir(parents=True, exist_ok=True)
    existing = sorted(history.glob("*.json"))
    revision = len(existing) + 1
    payload = {
        "revision": revision,
        "overlay": overlay,
        "fix_plan": plan.model_dump(mode="json") if plan else None,
        "restored_to": undo_of,
    }
    (history / f"{revision:05d}.json").write_text(json.dumps(payload, indent=1, sort_keys=True))
    text = json.dumps(overlay, indent=1, sort_keys=True)
    tmp = edits / "overlay.json.tmp"
    tmp.write_text(text)
    tmp.replace(edits / "overlay.json")
    return AppliedRevision(
        revision, sha256_hex(text.encode()), tuple(plan.impact.affected_unit_ids) if plan else ()
    )


def apply_fix_plan(project_dir: Path, plan: FixPlan) -> AppliedRevision:
    overlay = _read_overlay(project_dir)
    for op in plan.operations:
        if op.op == "replace_text_range":
            deliverable = deliverable_of_card(project_dir, op.unit_id)
            if deliverable is None:
                raise ApplyError(f"unit {op.unit_id} is not an editable copy unit in this project")
            copy = json.loads(
                (project_dir / "deliverables" / deliverable / "copy.json").read_text()
            )
            card = next(c for c in copy["cards"] if c["card_id"] == op.unit_id)
            if op.expected_before is not None and op.expected_before not in (
                None,
                "",
                card["text"][op.start : op.end],
            ):
                raise ApplyError("expected_before does not match the current text")
            new_text = (
                card["text"][: op.start] + op.replacement + card["text"][op.end :]
                if op.end
                else op.replacement
            )
            overlay.setdefault("copy", {}).setdefault(deliverable, {})[op.unit_id] = new_text
        elif op.op == "update_chart_encoding":
            overlay.setdefault("encoding", {})[op.scene_id] = {
                **overlay.get("encoding", {}).get(op.scene_id, {}),
                **op.encoding_patch,
            }
        elif op.op == "retime_beat":
            overlay.setdefault("timing", {})[op.scene_id] = op.duration_frames
        elif op.op == "change_scene_variant":
            overlay.setdefault("variant", {})[op.scene_id] = op.variant
        elif op.op == "suppress_deliverable":
            overlay.setdefault("suppressed", {})[op.deliverable_id] = op.reason
        else:
            raise ApplyError(f"operation {op.op} is not applicable to this project surface")
    return _write_revision(project_dir, overlay, plan)


def undo_last(project_dir: Path) -> AppliedRevision:
    """Undo walks the edit chain backwards; each undo is itself an append-only revision."""
    history = project_dir / "edits" / "history"
    revisions = sorted(history.glob("*.json")) if history.exists() else []
    if not revisions:
        raise ApplyError("nothing to undo")
    entries = [json.loads(r.read_text()) for r in revisions]
    last = entries[-1]
    if last.get("restored_to") is not None:
        target = last["restored_to"] - 1  # an undo of an undo walks one edit further back
    else:
        target = last["revision"] - 1
    if target < 0:
        raise ApplyError("already at the original revision")
    overlay = entries[target - 1]["overlay"] if target >= 1 else {}
    return _write_revision(project_dir, overlay, None, undo_of=target)


def current_overlay_hash(project_dir: Path) -> str:
    path = project_dir / "edits" / "overlay.json"
    return sha256_hex(path.read_bytes()) if path.exists() else "none"
