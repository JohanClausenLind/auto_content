"""Segmentation ids -> Blender pass_index / object colour."""

from __future__ import annotations

from typing import Any

from bl.assets import Entity


def assign(entities: list[Entity], seg_ids: dict[str, int]) -> dict[str, int]:
    """Apply the control-plane's deterministic assignment; environment pieces stay 0 unless
    flagged ``seg``. Returns ``{entity_id: seg_id}`` for everything that renders."""
    out: dict[str, int] = {}
    next_free = max(seg_ids.values(), default=0) + 1
    for ent in entities:
        if ent.kind == "environment":
            if ent.seg:
                ent.seg_id = next_free
                next_free += 1
            else:
                ent.seg_id = 0
        else:
            ent.seg_id = seg_ids.get(ent.entity_id, 0)
        out[ent.entity_id] = ent.seg_id
        for o in ent.objects:
            o.pass_index = ent.seg_id
            _set_color(o, ent.seg_id)
    return out


def _set_color(obj: Any, seg_id: int) -> None:
    # Object colour is only used by the Workbench OBJECT colour type; harmless otherwise.
    from palette import color_for_id

    r, g, b = color_for_id(seg_id)
    obj.color = (r / 255.0, g / 255.0, b / 255.0, 1.0)
