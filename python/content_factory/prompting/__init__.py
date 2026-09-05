"""Prompting: the templates a model role sends, and the guidance the pipeline learns.

Two halves, written by two hands and both re-exported here because `cli/main.py` and
`models/copywriter.py` each import from the package rather than from a submodule:

* :mod:`content_factory.prompting.templates` — every prompt this repo sends, versioned, plus the
  one `SYSTEM_INSTRUCTION` the gateway attaches to every structured call.
* :mod:`content_factory.prompting.learn` — turning review verdicts into proposed edits to
  ``skills/image/prompting/SKILL.md``. Proposals and an audit log, never a silent rewrite.

The re-exports are the package's API. `learn`'s names are listed explicitly rather than star-
imported so that adding one is a decision: this module is what two other modules import *from*,
and a name that appears here by accident is a name someone will depend on.
"""

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
    SYSTEM_INSTRUCTION,
    PromptTemplate,
    get,
)

__all__ = [
    "CAPTION",
    "CARDS",
    "PROMPTING_VERSION",
    "REGISTRY",
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
