"""Persona contracts (14.1): a governed, versioned asset. The PersonaFirewall is code and is NOT
represented here — no field on these models can weaken it. Adults only, enforced in the type."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, VersionedModel, WorkspaceId


class PersonaIdentity(SchemaModel):
    display_name: str = Field(min_length=1, max_length=100)
    pronouns: str = Field(default="they/them", max_length=40)
    presented_age: int = Field(ge=18, le=120)  # adults only — the schema refuses anything else
    languages: tuple[str, ...] = ("en",)
    platform_handles: dict[str, str] = Field(default_factory=dict)


class PersonaVoice(SchemaModel):
    tone: tuple[str, ...] = ("warm",)  # e.g. warm, teasing, dry, earnest
    vocabulary: Literal["simple", "everyday", "rich"] = "everyday"
    sentence_length: Literal["short", "medium", "long"] = "medium"
    emoji_policy: Literal["none", "rare", "moderate", "frequent"] = "rare"
    punctuation: Literal["standard", "no_full_stops", "relaxed"] = "standard"
    humor: Literal["none", "dry", "playful", "silly"] = "playful"
    pet_names: tuple[str, ...] = ()
    register_overrides: dict[str, str] = Field(default_factory=dict)  # platform -> register note


class PersonaBackstory(SchemaModel):
    """The ONLY biography any model ever sees (24.5)."""

    bio: str = Field(default="", max_length=4000)
    canonical_facts: tuple[str, ...] = ()
    tastes: tuple[str, ...] = ()
    running_jokes: tuple[str, ...] = ()


class PersonaBoundaries(SchemaModel):
    answer_directly: tuple[str, ...] = ()
    deflect: tuple[str, ...] = ()
    deflection_style: Literal["cute_playful", "direct"] = "cute_playful"
    hard_refuse: tuple[str, ...] = ()


class PersonaContentPolicy(SchemaModel):
    themes: tuple[str, ...] = ()
    categories: dict[str, float] = Field(default_factory=dict)  # category -> rotation weight
    anti_repetition_phrase_threshold: float = Field(default=0.35, gt=0, le=1)

    @model_validator(mode="after")
    def _weights(self) -> PersonaContentPolicy:
        if self.categories and any(w <= 0 for w in self.categories.values()):
            msg = "rotation weights must be positive"
            raise ValueError(msg)
        return self


class Persona(VersionedModel):
    persona_id: OpaqueId
    workspace_id: WorkspaceId
    revision: int = Field(ge=1)
    identity: PersonaIdentity
    voice: PersonaVoice = PersonaVoice()
    backstory: PersonaBackstory = PersonaBackstory()
    boundaries: PersonaBoundaries = PersonaBoundaries()
    content_policy: PersonaContentPolicy = PersonaContentPolicy()
    disclosure: Literal["disclose_on_ask", "deflect"] = (
        "disclose_on_ask"  # no claim-human mode exists
    )


class PersonaFieldChange(SchemaModel):
    path: str = Field(min_length=1)  # e.g. "voice.tone"
    before: str
    after: str
    reason: str = Field(min_length=1, max_length=300)


class PersonaRevisionDiff(SchemaModel):
    """persona.revise output: shown to the operator BEFORE apply; applying makes revision N+1."""

    persona_id: OpaqueId
    base_revision: int
    changes: tuple[PersonaFieldChange, ...] = Field(min_length=1)
    plain_language: str = Field(min_length=1, max_length=2000)


class RemakeProposal(SchemaModel):
    """Retroactive consistency (14.1): queued/unpublished items that no longer fit; ASK, never act."""  # noqa: E501

    item_id: OpaqueId
    item_kind: Literal["queued_script", "reply_draft", "queued_thumbnail", "open_draft"]
    conflicts_with: tuple[str, ...] = Field(min_length=1)  # changed paths
    excerpt: str = Field(max_length=300)
