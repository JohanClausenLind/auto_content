"""Prompting: the templates a model role sends, and the guidance the pipeline learns."""

from __future__ import annotations

from content_factory.prompting.learn import (
    GuidanceProposal,
    PromptLesson,
    ProposalRefusedError,
    apply_proposal,
    auto_apply,
    lessons_from_review,
    load_batches,
    load_proposal,
    render_proposal,
    write_proposal,
)
from content_factory.prompting.templates import (
    CAPTION,
    CARDS,
    PROMPTING_VERSION,
    REGISTRY,
    SET_REVIEW,
    SYSTEM_INSTRUCTION,
    PromptTemplate,
    get,
)

__all__ = [
    "CAPTION",
    "CARDS",
    "PROMPTING_VERSION",
    "REGISTRY",
    "SET_REVIEW",
    "SYSTEM_INSTRUCTION",
    "GuidanceProposal",
    "PromptLesson",
    "PromptTemplate",
    "ProposalRefusedError",
    "apply_proposal",
    "auto_apply",
    "get",
    "lessons_from_review",
    "load_batches",
    "load_proposal",
    "render_proposal",
    "write_proposal",
]
