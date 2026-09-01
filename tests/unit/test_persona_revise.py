"""Persona Revision Box (14.1): plain language → typed diff shown before apply; retroactive
consistency proposes remakes of queued work and ASKS; published content untouched; adults only."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from content_factory.personas.revise import (
    QueuedItem,
    ReviseError,
    apply_diff,
    consistency_check,
    map_persona_feedback,
)
from content_factory.schemas.personas import Persona, PersonaIdentity, PersonaVoice

PERSONA = Persona(
    persona_id="per_mia00000001",
    workspace_id="ws_demo00000001",
    revision=3,
    identity=PersonaIdentity(display_name="Mia", pronouns="she/her", presented_age=24),
    voice=PersonaVoice(
        tone=("warm", "formal"),
        sentence_length="long",
        emoji_policy="moderate",
        pet_names=("darling", "sunshine"),
    ),
)


def test_adults_only_is_enforced_by_the_type() -> None:
    with pytest.raises(ValidationError):
        PersonaIdentity(display_name="X", presented_age=17)


def test_plain_language_maps_to_a_typed_diff_and_applies_as_new_revision() -> None:
    diff = map_persona_feedback(
        "she sounds too formal, more teasing, shorter messages, never uses full stops", PERSONA
    )
    paths = {c.path for c in diff.changes}
    assert paths == {"voice.tone", "voice.sentence_length", "voice.punctuation"}
    tone = next(c for c in diff.changes if c.path == "voice.tone")
    assert "formal" not in tone.after.split(", ") and "teasing" in tone.after.split(", ")
    assert diff.base_revision == 3 and "→" in diff.plain_language
    updated = apply_diff(PERSONA, diff)
    assert updated.revision == 4
    assert updated.voice.sentence_length == "short" and updated.voice.punctuation == "no_full_stops"
    assert PERSONA.voice.sentence_length == "long"  # original immutable
    # A stale diff (wrong base revision) refuses to apply.
    with pytest.raises(ReviseError, match="re-review"):
        apply_diff(updated, diff)


def test_pet_name_removal_and_unmappable_feedback() -> None:
    diff = map_persona_feedback("please stop calling people darling", PERSONA)
    change = diff.changes[0]
    assert (
        change.path == "voice.pet_names"
        and "darling" not in change.after
        and "sunshine" in change.after
    )
    with pytest.raises(ReviseError, match="couldn't map"):
        map_persona_feedback("hmm just make her better somehow", PERSONA)


def test_consistency_check_proposes_remakes_and_never_touches_published() -> None:
    diff = map_persona_feedback(
        "shorter messages, never uses full stops, stop calling people darling", PERSONA
    )
    items = [
        QueuedItem(
            "scr_000000000001",
            "queued_script",
            "Good morning darling. I was thinking about the long walk we could take together, and honestly it might be the best part of my whole entire week ahead.",
        ),
        QueuedItem(
            "scr_000000000002",
            "queued_script",
            "hey you — quick hello before practice, thinking of you",
        ),
        QueuedItem(
            "scr_000000000003", "queued_script", "Good morning darling. Miss you.", published=True
        ),
        QueuedItem(
            "rd_0000000000004",
            "reply_draft",
            "That is such a lovely thing to say. Thank you truly.",
        ),
    ]
    proposals = consistency_check(diff, items)
    ids = {p.item_id for p in proposals}
    assert "scr_000000000001" in ids and "scr_000000000003" not in ids  # published item untouched
    scr1 = next(p for p in proposals if p.item_id == "scr_000000000001")
    assert "voice.pet_names" in scr1.conflicts_with and "voice.punctuation" in scr1.conflicts_with
    assert "scr_000000000002" not in ids  # already fits the new voice
    rd = next(p for p in proposals if p.item_id == "rd_0000000000004")
    assert rd.conflicts_with == ("voice.punctuation",)
