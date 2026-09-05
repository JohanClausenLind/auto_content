"""The channel's default editorial style kit and the prompt builders that enforce it.

``image_prompt`` appends the fixed style suffix so generated stills composite cleanly under
deterministic overlays. ``video_prompt`` assembles the one-paragraph chronological
cinematography description generative video models expect, and refuses prompts that omit a
component or exceed the length the models handle well. Charts, equations, maps and factual
diagrams are never requested from an image model — those stay data-driven renders.
"""

from __future__ import annotations

from content_factory.schemas.style_kit import EditorialStyleKit

IMAGE_STYLE_SUFFIX = (
    "technical editorial illustration, precise geometric composition, dark navy-black field, "
    "warm off-white materials and linework, cyan and amber semantic accents, restrained coral "
    "only for failure states, clean vector-like silhouettes with subtle tactile grain, high "
    "contrast, generous negative space, cinematic but physically plausible lighting, "
    "no typography, no labels, no watermark, no logo, no fake interface, no neon cyberpunk, "
    "no visual clutter, designed for later motion-graphics compositing"
)

EDITORIAL_KIT = EditorialStyleKit(kit_id="editorial-v1", image_style_suffix=IMAGE_STYLE_SUFFIX)

VIDEO_PROMPT_MAX_WORDS = 200


def image_prompt(subject: str, *, kit: EditorialStyleKit = EDITORIAL_KIT) -> str:
    """Subject description plus the style suffix. The subject must not ask for text/labels."""
    subject = " ".join(subject.split())
    if not subject:
        msg = "image prompt needs a subject"
        raise ValueError(msg)
    return f"{subject}, {kit.image_style_suffix}"


def video_prompt(
    *,
    action: str,
    object_motion: str,
    appearance: str,
    environment: str,
    camera: str,
    lighting: str,
    end_state: str,
) -> str:
    """One chronological cinematography paragraph under 200 words, every component present."""
    parts = {
        "action": action,
        "object_motion": object_motion,
        "appearance": appearance,
        "environment": environment,
        "camera": camera,
        "lighting": lighting,
        "end_state": end_state,
    }
    cleaned: list[str] = []
    for name, text in parts.items():
        text = " ".join(text.split())
        if not text:
            msg = f"video prompt component '{name}' is empty"
            raise ValueError(msg)
        cleaned.append(text if text.endswith((".", "!", "?")) else text + ".")
    paragraph = " ".join(cleaned)
    words = len(paragraph.split())
    if words > VIDEO_PROMPT_MAX_WORDS:
        msg = f"video prompt is {words} words; the limit is {VIDEO_PROMPT_MAX_WORDS}"
        raise ValueError(msg)
    return paragraph


def clip_seconds_ok(seconds: float, *, kit: EditorialStyleKit = EDITORIAL_KIT) -> bool:
    """Generated clips stay short (image-to-video shots, not whole scenes)."""
    r = kit.motion.generated_clip_seconds
    return r.lo <= seconds <= r.hi
