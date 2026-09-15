"""Named art directions for the anchor stage."""

from __future__ import annotations

STYLE_PRESETS: dict[str, str] = {
    # Realistic.
    "photographic": (
        "colour photograph on 35 mm film, fine grain visible in the flat areas, natural available "
        "light, physically convincing materials with a true specular response, full tonal range "
        "with detail held in both the shadows and the highlights, soft halation where a highlight "
        "clips, every surface carrying its own dust, wear and small imperfections"
    ),
    # The set version of the same look.
    "photographic_set": (
        "colour photograph on 35 mm film, fine grain visible in the flat areas, natural available "
        "light, physically convincing materials with a true specular response, full tonal range, "
        "one light direction and one colour response held from image to image, the same lens and "
        "the same working distance in every frame"
    ),
    # A style clause says how the picture is *rendered* and must not name anything the picture could
    # contain: the style leads the prompt (`_anchor_prompt`), so a noun here outranks the subject.
    "cinematic": (
        "anamorphic widescreen, a long lens held wide open so the background falls softly away, "
        "motion-picture colour response, lit only by sources inside the scene, deep shadows that "
        "still hold detail, muted naturalistic colour, fine grain, faint halation around the "
        "practicals, surfaces scuffed and lived-in"
    ),
    # The twelve presets had no dark one, and every photographic clause here asks for the opposite:
    # "full tonal range with detail held in both the shadows.
    "low_key": (
        "low-key photography, one hard light source and everything outside it falling to true "
        "black, deep crushed shadows with no lift and no fill, high contrast, fine grain in the "
        "lit areas, the source flaring slightly into the lens"
    ),
    "documentary": (
        "candid documentary photograph, available light, neutral true-to-life colour, unposed, "
        "slight motion blur, full tonal range, visible sensor grain, an ordinary cluttered "
        "background the photographer did not arrange"
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
        "no colour, drawn from life, soft edges, heavy grain"
    ),
    "watercolour": (
        "loose watercolour on cold-press paper, wet-in-wet bleeding, no outlines, large areas of "
        "bare white paper, three or four transparent washes, granulating pigment, delicate"
    ),
    "oil": (
        "oil painting, thick impasto, visible brush marks and palette-knife edges, no outlines, "
        "muted earth palette, Nordic painting tradition, matte canvas texture"
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
        "gouache washes, restrained palette, Studio-era anime background art, "
        "no photographic detail"
    ),
    # NOTE the wording, and what it cost: this clause used to end "characters drawn the same way in
    # every frame, painted background held still behind them".
    "anime_limited": (
        "cel-shaded animation, flat colour fills in two tone steps, clean even line weight, "
        "a small fixed palette, simple graphic shadow shapes with hard edges, consistent "
        "character design and proportions, a painted background behind them"
    ),
    "silhouette": (
        "graphic poster, two flat colours and a paper ground, the subject reduced to a silhouette, "
        "no interior detail, hard geometric shapes, mid-century screenprint, generous empty space"
    ),
}


# **Do not add a "no lettering" clause here.** Measured 2026-09-10, same subject and seed: the
# signage gibberish did not change; the skill server has no negative-prompt field (journal).


def style_name_for(prompt: str) -> str:
    """The preset name a style prompt came from, or `""` for a hand-written one."""
    wanted = prompt.strip()
    for name, preset in STYLE_PRESETS.items():
        if preset == wanted:
            return name
    return ""


def resolve_style(value: str) -> str:
    """A preset name, or a style prompt written out in full."""
    value = value.strip()
    if value in STYLE_PRESETS:
        return STYLE_PRESETS[value]
    if len(value.split()) < 4:
        known = ", ".join(sorted(STYLE_PRESETS))
        msg = f"unknown style preset {value!r}; known: {known} (or write the prompt out in full)"
        raise ValueError(msg)
    return value
