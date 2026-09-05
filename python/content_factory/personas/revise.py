"""persona.revise (14.1): plain language → typed PersonaRevisionDiff, shown before apply, plus
retroactive consistency proposals over queued/unpublished work. Published content is never touched.
Deterministic mapper (the production model role sits behind the same contract)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from content_factory.schemas.personas import (
    Persona,
    PersonaFieldChange,
    PersonaRevisionDiff,
    RemakeProposal,
)

_RULES: list[tuple[re.Pattern[str], str, str, str]] = [
    (
        re.compile(r"\b(too formal|less formal|loosen up)\b", re.I),
        "voice.tone",
        "-formal +casual",
        "operator: register too formal",
    ),
    (
        re.compile(r"\bmore teasing\b", re.I),
        "voice.tone",
        "+teasing",
        "operator asked for more teasing",
    ),
    (
        re.compile(r"\b(shorter (messages|sentences)|keep it short)\b", re.I),
        "voice.sentence_length",
        "short",
        "operator asked for shorter messages",
    ),
    (
        re.compile(r"\blonger (messages|sentences)\b", re.I),
        "voice.sentence_length",
        "long",
        "operator asked for longer messages",
    ),
    (
        re.compile(r"\bnever uses? full stops?\b|\bno periods\b", re.I),
        "voice.punctuation",
        "no_full_stops",
        "operator: drop sentence-final full stops",
    ),
    (
        re.compile(r"\b(more emojis?)\b", re.I),
        "voice.emoji_policy",
        "frequent",
        "operator asked for more emoji",
    ),
    (
        re.compile(r"\b(fewer|less|no) emojis?\b", re.I),
        "voice.emoji_policy",
        "rare",
        "operator asked for fewer emoji",
    ),
    (
        re.compile(r"\bstop calling (people|fans|them) ([a-z]+)\b", re.I),
        "voice.pet_names",
        "-{2}",
        "operator removed a pet name",
    ),
]


class ReviseError(Exception):
    pass


def map_persona_feedback(feedback: str, persona: Persona) -> PersonaRevisionDiff:
    changes: list[PersonaFieldChange] = []
    tones = list(persona.voice.tone)
    tone_reasons: list[str] = []
    for pattern, path, action, reason in _RULES:
        m = pattern.search(feedback)
        if not m:
            continue
        if path == "voice.tone":
            # Tone edits accumulate onto one working copy → exactly ONE voice.tone change.
            if action == "-formal +casual":
                tones = [t for t in tones if t != "formal"] or ["warm"]
                if "casual" not in tones:
                    tones.append("casual")
            elif action == "+teasing" and "teasing" not in tones:
                tones.append("teasing")
            tone_reasons.append(reason)
        elif path == "voice.pet_names":
            name = m.group(2).lower()
            if name in persona.voice.pet_names:
                remaining = tuple(p for p in persona.voice.pet_names if p != name)
                changes.append(
                    PersonaFieldChange(
                        path=path,
                        before=", ".join(persona.voice.pet_names),
                        after=", ".join(remaining) or "(none)",
                        reason=f"{reason}: {name!r}",
                    )
                )
        else:
            before = str(getattr(persona.voice, path.split(".")[1]))
            if before != action:
                changes.append(
                    PersonaFieldChange(path=path, before=before, after=action, reason=reason)
                )
    if tone_reasons and tuple(tones) != persona.voice.tone:
        changes.insert(
            0,
            PersonaFieldChange(
                path="voice.tone",
                before=", ".join(persona.voice.tone),
                after=", ".join(tones),
                reason="; ".join(tone_reasons),
            ),
        )
    if not changes:
        raise ReviseError(
            "I couldn't map that to persona fields — say what should change (tone, message length, emoji, punctuation, pet names)."  # noqa: E501
        )
    return PersonaRevisionDiff(
        persona_id=persona.persona_id,
        base_revision=persona.revision,
        changes=tuple(changes),
        plain_language="; ".join(f"{c.path}: {c.before} → {c.after}" for c in changes),
    )


def apply_diff(persona: Persona, diff: PersonaRevisionDiff) -> Persona:
    if diff.base_revision != persona.revision:
        raise ReviseError(
            f"diff was made against revision {diff.base_revision}, persona is at {persona.revision} — re-review"  # noqa: E501
        )
    voice = persona.voice
    for c in diff.changes:
        field = c.path.split(".", 1)[1]
        if field == "tone":
            voice = voice.model_copy(update={"tone": tuple(t.strip() for t in c.after.split(","))})
        elif field == "pet_names":
            names = () if c.after == "(none)" else tuple(t.strip() for t in c.after.split(","))
            voice = voice.model_copy(update={"pet_names": names})
        else:
            voice = voice.model_copy(update={field: c.after})
    return persona.model_copy(update={"voice": voice, "revision": persona.revision + 1})


@dataclass(frozen=True)
class QueuedItem:
    item_id: str
    kind: str  # queued_script | reply_draft | queued_thumbnail | open_draft
    text: str
    published: bool = False


def consistency_check(diff: PersonaRevisionDiff, items: list[QueuedItem]) -> list[RemakeProposal]:
    """Which QUEUED/UNPUBLISHED items conflict with the new voice? Proposes; never remakes."""
    changed = {c.path: c for c in diff.changes}
    proposals: list[RemakeProposal] = []
    for item in items:
        if item.published:
            continue  # published content is never silently changed
        conflicts: list[str] = []
        if (
            "voice.punctuation" in changed
            and changed["voice.punctuation"].after == "no_full_stops"
            and re.search(r"\.\s|\.$", item.text.strip())
        ):
            conflicts.append("voice.punctuation")
        if "voice.sentence_length" in changed and changed["voice.sentence_length"].after == "short":
            words = item.text.split()
            sentences = max(1, item.text.count(".") + item.text.count("!") + item.text.count("?"))
            if len(words) / sentences > 18:
                conflicts.append("voice.sentence_length")
        if "voice.pet_names" in changed:
            removed = set(changed["voice.pet_names"].before.split(", ")) - set(
                changed["voice.pet_names"].after.split(", ")
            )
            if any(
                re.search(rf"\b{re.escape(name)}\b", item.text, re.I)
                for name in removed
                if name and name != "(none)"
            ):
                conflicts.append("voice.pet_names")
        if "voice.emoji_policy" in changed and changed["voice.emoji_policy"].after == "rare":
            if len(re.findall(r"[\U0001F000-\U0001FAFF]", item.text)) > 2:
                conflicts.append("voice.emoji_policy")
        if conflicts:
            proposals.append(
                RemakeProposal(
                    item_id=item.item_id,
                    item_kind=item.kind,  # type: ignore[arg-type]
                    conflicts_with=tuple(sorted(set(conflicts))),
                    excerpt=item.text[:300],
                )
            )  # type: ignore[arg-type]
    return proposals
