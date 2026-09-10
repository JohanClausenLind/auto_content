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


EXEMPT_BY_KIND: dict[str, tuple[str, ...]] = {
    # A frame that moves or re-poses the subject cannot also preserve where the subject is and
    # what shape it is in. Measured on a six-view owl set (2026-09-09): three frames asked for
    # "head turned a quarter to the left", "head tilted up, pale throat exposed" and "leaning
    # forward, feathers flattened", and all three came back all but identical to the anchor —
    # because the sentence before them said "Do not alter: ... composition, subject anatomy".
    # The instruction was contradicting itself, and the model resolved it by preserving.
    "move_subject": ("composition", "subject anatomy"),
    "pose_change": ("composition", "subject anatomy"),
    # An expression is anatomy too, but only of the face: the body should hold, so only the
    # anatomy clause goes.
    "expression_change": ("subject anatomy",),
}
"""What a frame is allowed to change, by the kind of change it declares. Everything else in
:data:`PRESERVE_LIST` still holds — the camera, the crop, the palette, the background and the
lighting are what make the set a set."""


def compile_edit_instruction(delta: FrameDelta, lock: GenerationLock) -> str:
    exempt = EXEMPT_BY_KIND.get(delta.kind, ())
    preserved = ", ".join(item for item in PRESERVE_LIST if item not in exempt)
    style = f" Style block: {lock.style_prompt}." if lock.style_prompt else ""
    if delta.kind == "none":
        change = "Change nothing."
    else:
        change = f"Apply exactly ONE change: {delta.instruction}"
        if delta.subject_label:
            change += f" (subject: {delta.subject_label})"
        change += ". Everything else unchanged."
    return f"Preserve the reference image exactly. Do not alter: {preserved}.{style} {change}"
