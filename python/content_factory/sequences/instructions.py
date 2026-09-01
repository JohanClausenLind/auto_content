"""Edit-instruction compiler (16.6): the frame's declared delta becomes a verbose
preserve-everything instruction automatically — the operator never hand-writes it."""

from __future__ import annotations

from content_factory.schemas.sequences import FrameDelta, GenerationLock

PRESERVE_LIST = (
    "camera",
    "crop",
    "composition",
    "subject anatomy",
    "line style",
    "shading",
    "palette",
    "background",
    "lighting",
)


def compile_edit_instruction(delta: FrameDelta, lock: GenerationLock) -> str:
    preserved = ", ".join(PRESERVE_LIST)
    style = f" Style block: {lock.style_prompt}." if lock.style_prompt else ""
    if delta.kind == "none":
        change = "Change nothing."
    else:
        change = f"Apply exactly ONE change: {delta.instruction}"
        if delta.subject_label:
            change += f" (subject: {delta.subject_label})"
        change += ". Everything else unchanged."
    return f"Preserve the reference image exactly. Do not alter: {preserved}.{style} {change}"
