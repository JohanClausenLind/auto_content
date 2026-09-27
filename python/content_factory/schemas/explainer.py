"""The explainer plan: what the mechanism-first planner returns, as a contract.

The planner prompt (``docs/prompts/explainer-planner/prompt.md``) hands the model a JSON Schema
(``schema.json`` beside it), and this is the same contract as a Pydantic model, because contracts
start here. ``tests/unit/test_explainer.py`` validates the same plans against both, so the file the
model reads and the model the pipeline parses cannot drift apart unnoticed.

Flat on purpose, like the script writer's ``DraftBeat``: every field is required and "" stands for
"does not apply", which is the shape constrained decoding fills reliably. What the plan *means*
(what is on screen, which move lands where, whether the pacing holds) is not a type question;
``content_factory.explainer.check`` answers it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints

from content_factory.schemas.base import SchemaModel

ObjectRef = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
ObjectRefOrEmpty = Annotated[str, StringConstraints(pattern=r"^([a-z][a-z0-9_]*)?$")]
BeatRef = Annotated[str, StringConstraints(pattern=r"^b[0-9]{2}$")]

ColorRole = Literal[
    "neutral", "software", "privileged", "hardware", "data", "fault", "accent_1", "accent_2"
]
Primitive = Literal[
    "box",
    "label",
    "arrow",
    "memory_row",
    "register",
    "code_block",
    "callout",
    "graph_line",
    "graph_bar",
    "data_packet",
    "chip",
    "process",
    "layer_boundary",
    "fault_marker",
    "timeline",
    "state_table",
]
UpdateOp = Literal[
    "reveal",
    "highlight",
    "update",
    "morph",
    "split",
    "merge",
    "trace",
    "branch",
    "compare",
    "fault",
    "dismiss",
]
Transition = Literal[
    "reveal",
    "highlight",
    "update",
    "morph",
    "split",
    "merge",
    "trace",
    "branch",
    "compare",
    "fault",
    "dismiss",
    "zoom_in",
    "zoom_out",
    "cut",
]
BeatType = Literal[
    "question_hook",
    "common_assumption",
    "system_overview",
    "normal_flow",
    "edge_case",
    "exception_path",
    "layer_zoom",
    "comparison",
    "resolution",
    "takeaway",
]


class SpineStep(SchemaModel):
    layer: str
    claim: str


class ColorSemantic(SchemaModel):
    role: ColorRole
    meaning: str


class ExplainerObject(SchemaModel):
    # Every dump writes the wire name, so a fixture export or a content hash never says ``from_``.
    model_config = ConfigDict(serialize_by_alias=True)

    id: ObjectRef
    primitive: Primitive
    label: str
    parent: ObjectRefOrEmpty
    cell: Annotated[str, StringConstraints(pattern=r"^([0-9]+)?$")]
    layer: str
    color_role: ColorRole
    # ``from`` is a Python keyword; the wire name is what the model writes.
    from_: ObjectRefOrEmpty = Field(alias="from")
    to: ObjectRefOrEmpty


class VisualUpdate(SchemaModel):
    at_sentence: int = Field(ge=0)
    op: UpdateOp
    targets: tuple[ObjectRef, ...] = Field(min_length=1)
    into: ObjectRefOrEmpty
    value: str
    note: str


class ExplainerBeat(SchemaModel):
    id: BeatRef
    type: BeatType
    goal: str
    question: str
    new_concept: str
    mechanism: str
    layer: str
    frame: ObjectRef
    narration: tuple[str, ...] = Field(min_length=1)
    visual_updates: tuple[VisualUpdate, ...]
    transition_in: Transition
    transition_out: Transition
    est_seconds: int = Field(ge=1)


class ClaimToVerify(SchemaModel):
    claim: str
    beat: BeatRef
    sentence: int = Field(ge=-1)
    object: ObjectRefOrEmpty


class ExplainerPlan(SchemaModel):
    """One mechanism-first explainer: its question, its scene graph, and its beats."""

    title_question: str
    common_assumption: str
    answer: str
    takeaway: str
    audience: str
    target_duration_s: int = Field(ge=60, le=600)
    layers: tuple[str, ...] = Field(min_length=1)
    explanation_spine: tuple[SpineStep, ...] = Field(min_length=1)
    color_semantics: tuple[ColorSemantic, ...] = Field(min_length=1)
    objects: tuple[ExplainerObject, ...] = Field(min_length=1, max_length=30)
    beats: tuple[ExplainerBeat, ...] = Field(min_length=1, max_length=16)
    claims_to_verify: tuple[ClaimToVerify, ...]
    notes: str
