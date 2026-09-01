"""Originality engine fixtures (28): exact copies, paraphrases, swapped nouns, shared factual
language, templates, intended adaptations, perceptual image matches, companion mass production."""

from __future__ import annotations

from PIL import Image, ImageDraw

from content_factory.originality.fingerprint import (
    Comparison,
    OriginalityVerdict,
    Thresholds,
    compare,
    decide,
    dhash,
    fingerprint_script,
    hamming,
)

BEATS_A = [
    "In 2025, wind supplied about a fifth of Sweden's electricity.",
    "That share has roughly doubled since 2018.",
    "Three things drove it: new turbines, better siting, and cheaper finance.",
]
KINDS_A = ["title", "big_number", "bullet_sequence"]


def test_exact_copy_blocks() -> None:
    fp = fingerprint_script(BEATS_A, KINDS_A)
    decision = decide([compare(fp, fp, "post-2025-03")])
    assert decision.verdict == OriginalityVerdict.too_similar and decision.blocking
    assert "phrases match" in decision.explanations[0]


def test_swapped_nouns_still_block() -> None:
    swapped = [b.replace("Sweden", "Norway").replace("wind", "hydro") for b in BEATS_A]
    decision = decide(
        [
            compare(
                fingerprint_script(swapped, KINDS_A), fingerprint_script(BEATS_A, KINDS_A), "post-x"
            )
        ]
    )
    assert decision.verdict in {
        OriginalityVerdict.too_similar,
        OriginalityVerdict.needs_differentiation,
    }
    assert decision.verdict == OriginalityVerdict.too_similar  # only two nouns changed


def test_same_hook_and_beat_order_needs_differentiation() -> None:
    new_beats = [
        "In 2025, wind supplied about a fifth of Sweden's electricity.",  # same hook
        "Solar output grew forty percent in two years.",
        "Grid fees, permitting reform, and storage each played a part in the shift.",
    ]
    decision = decide(
        [
            compare(
                fingerprint_script(new_beats, KINDS_A),
                fingerprint_script(BEATS_A, KINDS_A),
                "post-y",
            )
        ]
    )
    assert decision.verdict == OriginalityVerdict.needs_differentiation
    assert not decision.blocking
    assert "hook" in decision.explanations[0]


def test_fresh_story_is_original_and_shared_factual_language_is_fine() -> None:
    fresh = [
        "Sweden's grid fees fell for the first time in a decade.",
        "Industrial demand is the quiet driver behind the change.",
        "Sources: Svenska kraftnat annual report.",
    ]
    decision = decide(
        [
            compare(
                fingerprint_script(fresh, ["title", "callout", "source_card"]),
                fingerprint_script(BEATS_A, KINDS_A),
                "post-z",
            )
        ]
    )
    assert decision.verdict == OriginalityVerdict.original


def test_declared_adaptation_is_acceptable_but_undeclared_blocks() -> None:
    shorter = [BEATS_A[0], BEATS_A[2]]
    fp_new = fingerprint_script(shorter, ["title", "bullet_sequence"])
    fp_old = fingerprint_script(BEATS_A, KINDS_A)
    undeclared = decide([compare(fp_new, fp_old, "post-long")])
    assert undeclared.verdict == OriginalityVerdict.too_similar
    declared = decide([compare(fp_new, fp_old, "post-long")], declared_adaptation_of="post-long")
    assert declared.verdict == OriginalityVerdict.acceptable_adaptation and not declared.blocking


def test_companion_mass_production_risk() -> None:
    base = ["Good morning sunshine, hope your day sparkles.", "Thinking of you this morning."]
    variants = [
        ["Good morning sunshine, hope your day sparkles!", "Thinking of you again this morning."],
        [
            "Good morning sunshine, hope your day truly sparkles.",
            "Thinking of you this fine morning.",
        ],
        ["Good morning sunshine — hope your day sparkles.", "Thinking about you this morning."],
    ]
    fp = fingerprint_script(base, ["callout", "callout"])
    comparisons = [
        compare(fp, fingerprint_script(v, ["callout", "callout"]), f"clip-{i}")
        for i, v in enumerate(variants)
    ]
    decision = decide(
        comparisons, thresholds=Thresholds(too_similar_phrase=0.35, mass_production_min_matches=3)
    )
    assert decision.verdict == OriginalityVerdict.mass_production_risk and decision.blocking
    assert "mass-production" in " ".join(decision.explanations)


def test_perceptual_image_match_contributes() -> None:
    img = Image.new("RGB", (128, 128), (240, 238, 232))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 30, 100, 60], fill=(20, 20, 30))
    near = img.copy()
    ImageDraw.Draw(near).text((24, 90), "v2", fill=(20, 20, 30))
    assert hamming(dhash(img), dhash(near)) <= 8
    fp_a = fingerprint_script(["Same layout, barely changed words on the card."], ["single"])
    fp_b = fingerprint_script(["Same layout, barely different words on this card."], ["single"])
    c = compare(fp_a, fp_b, "cover-1", new_image=img, prior_image=near)
    assert c.image_distance is not None and c.image_distance <= 8
    decision = decide([c])
    assert decision.verdict == OriginalityVerdict.too_similar


def test_model_can_never_override_blocking() -> None:
    decision = decide(
        [
            Comparison(
                against="x",
                phrase_overlap=0.9,
                frame_overlap=0.9,
                hook_overlap=1.0,
                beat_order_match=1.0,
            )
        ]
    )
    assert (
        decision.blocking
    )  # `blocking` derives from the verdict alone; there is no override input
