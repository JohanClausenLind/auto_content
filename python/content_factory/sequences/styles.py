"""Named art directions for the anchor stage.

The style prompt is the single biggest lever on what a generated film looks like, and it has to be
the *same* string for every frame or the sequence drifts — so it lives here as a named preset
rather than being retyped per run. ``GenerationLock`` freezes whichever string was used, and the
anchor's input hash includes it, so changing the look re-draws every frame and nothing else.

These are deliberately different families, not variations of one look: three realistic ones
first, then an outline style, a tonal style, a paint style, a print style. Each excludes what the
model otherwise reaches for by default — for the illustration styles that means photographic
lighting and gradient shading; for the realistic ones it means outlines and posterisation.
"""

from __future__ import annotations

STYLE_PRESETS: dict[str, str] = {
    # Realistic. The tonal clauses are not decoration: the illustration presets below collapse the
    # midtones (one measured 31 % of pixels crushed to near-black and only 36 % midtones, where a
    # photograph runs 60-80 %), so asking explicitly for detail in both shadows and highlights is
    # what keeps the range open. Negations name what the model otherwise reaches for by default
    # here — outlines and posterisation — rather than generic quality words.
    "photographic": (
        "photographic, natural available light, physically convincing materials, wet surfaces "
        "with real specular reflections, full tonal range with detail held in both the shadows "
        "and the highlights, shallow depth of field, no outlines, no posterisation, "
        "no illustration, not a drawing"
    ),
    "cinematic": (
        "photographed on a cinema camera, anamorphic, practical street lighting only, deep "
        "shadows that still hold detail, muted naturalistic colour, fine grain, no outlines, "
        "no posterisation, not a drawing"
    ),
    "documentary": (
        "candid documentary photograph, available light, neutral colour, natural skin tones, "
        "unposed, slight motion blur, full tonal range, no stylisation, no outlines, "
        "not a drawing"
    ),
    "ink_wash": (
        "hand-drawn ink and watercolour illustration, confident brush line, flat washes, "
        "paper texture, consistent character design, no 3D render look, no photographic lighting"
    ),
    "woodblock": (
        "Japanese woodblock print, ukiyo-e, flat unmodulated colour areas, single-weight carved "
        "outline, visible woodgrain and paper fibre, muted indigo and ochre, no gradients, "
        "no shading"
    ),
    "charcoal": (
        "charcoal and white chalk on grey toned paper, monochrome, smudged tone, torn hatching, "
        "no colour, life-drawing study, soft edges, heavy grain"
    ),
    "watercolour": (
        "loose watercolour on cold-press paper, wet-in-wet bleeding, no outlines, large areas of "
        "bare white paper, three or four transparent washes, granulating pigment, delicate"
    ),
    "oil": (
        "oil painting, thick impasto, visible brush marks and palette-knife edges, no outlines, "
        "muted earth palette, Nordic figurative painting, matte canvas texture"
    ),
    "riso": (
        "two-colour risograph print, fluorescent pink and teal only, coarse halftone dots, "
        "visible misregistration, flat paper stock, no black, no gradients"
    ),
    "pencil": (
        "graphite pencil storyboard sketch, loose construction lines left visible, cross-hatched "
        "shadow, no colour, off-white sketchbook paper, unfinished edges"
    ),
    "cel": (
        "hand-painted animation background, cel shading, two flat tone steps, clean thin line, "
        "gouache sky, restrained palette, Studio-era anime background art, no photographic detail"
    ),
    "silhouette": (
        "graphic poster, two flat colours and a paper ground, figures reduced to silhouettes, "
        "no interior detail, hard geometric shapes, mid-century screenprint, generous empty space"
    ),
}


def style_name_for(prompt: str) -> str:
    """The preset name a style prompt came from, or `""` for a hand-written one.

    The reverse of :func:`resolve_style`, and the reason it exists is that `GenerationLock` freezes
    the *prompt* — the string the model saw, which is the right thing to freeze — while a per-style
    setting (drift thresholds, for one) has to be keyed by the name. A prompt written out in full
    has no name and therefore no per-style override, which is the honest answer: nobody has
    measured it.
    """
    wanted = prompt.strip()
    for name, preset in STYLE_PRESETS.items():
        if preset == wanted:
            return name
    return ""


def resolve_style(value: str) -> str:
    """A preset name, or a style prompt written out in full.

    A name that is not a preset would otherwise be sent to the model as a two-word prompt and
    quietly produce something unrelated, so an unknown short token is refused rather than used.
    """
    value = value.strip()
    if value in STYLE_PRESETS:
        return STYLE_PRESETS[value]
    if len(value.split()) < 4:
        known = ", ".join(sorted(STYLE_PRESETS))
        msg = f"unknown style preset {value!r}; known: {known} (or write the prompt out in full)"
        raise ValueError(msg)
    return value
