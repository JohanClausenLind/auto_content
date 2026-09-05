"""Every prompt this repo sends to a language model, in one place, with a version each.

Before this, prompts were f-strings inline in the module that happened to need one:
`models/copywriter.py` had three, `models/scriptwriter.py` built a fourth over sixty lines,
`shots/prompt_compile.py` a fifth for the image and video models. Three consequences, and the
third is the one that bit:

* **Nothing shared.** The instruction "do not invent a number" had to be written again in each,
  and the wording drifted — so the same rule was stated three ways, none of them authoritative.
* **Nothing versioned.** A reworded prompt changes the output and every cache key that includes
  the prompt text got the change for free, but nothing *said* the prompt had changed, so a
  measurement taken last week could not be compared with one taken today.
* **No system message at all** on the structured path. When Ollama's constrained decoding went in
  (2026-09-08) the schema stopped being pasted into a system message, and the system message went
  with it — including the parts that were never about the schema. So the shared rules about
  inventing figures and about citing were, for a while, sent to nobody.

:data:`SYSTEM_INSTRUCTION` is the one text every role sends, and it holds only what is true for
**all** of them. A rule that applies to one role belongs in that role's template, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

PROMPTING_VERSION = "1.0.0"
"""Bumped when :data:`SYSTEM_INSTRUCTION` or any template's text changes.

It belongs in a cache key next to the prompt itself, the same way `PROMPT_COMPILER_VERSION` sits
in a clip's key: a reworded instruction produces different output from identical inputs, and a
cache that cannot see the rewording serves the old answer for ever.
"""

SYSTEM_INSTRUCTION = (
    "You are part of a documentary production pipeline. Every number, name and date you write"
    " will be checked against a cited source before it reaches a viewer, and anything that fails"
    " that check is discarded along with the sentence it was in.\n"
    "Never invent a figure, a quotation, a place name or a date. If the material you were given"
    " does not support a claim, leave it out and say nothing in its place — a shorter piece is"
    " correct, an invented one is not.\n"
    "Write plainly. No marketing register, no rhetorical questions to the reader, no words like"
    " 'delve', 'unlock', 'journey' or 'landscape' used figuratively."
)
"""The one instruction every model role sends, and nothing role-specific.

Each sentence is here because it is true for every role. The first states the consequence rather
than a rule, because the validators really do discard beats that fail — the copywriter's live run
wrote "about 18%" where the brief said a fifth, which is the failure this sentence is about. It
says "sentence" and not "beat" because a beat is the scriptwriter's unit and this text is sent to
every role — a test asserts the shared instruction carries no role-specific vocabulary, and it
caught exactly that word here.

The last is a house-style rule, and it is here rather than in each template because a film that
sounds like an advertisement sounds that way in the captions, the article and the narration alike.
"""


@dataclass(frozen=True)
class PromptTemplate:
    """One prompt, named, versioned and rendered from keyword arguments only.

    ``format`` rather than an f-string at the call site, so the template's text can be read,
    diffed and versioned without running the code that uses it — and so a missing input is a
    `KeyError` naming the field rather than a silently empty string in the middle of a prompt.
    """

    template_id: str
    version: str
    purpose: str
    text: str

    def render(self, **values: object) -> str:
        return self.text.format(**values)

    def system(self) -> str:
        """The shared instruction. A template that needs more says so in its own text."""
        return SYSTEM_INSTRUCTION


CAPTION = PromptTemplate(
    template_id="copy.caption",
    version="1.0.0",
    purpose="One social caption plus alt text for a finished picture.",
    text=(
        "Write a caption for a {platform} post about: {topic}\n"
        "Audience: {audience}. Tone: {tone}.\n"
        "At most {max_chars} characters. No hashtags unless the topic is a named event.\n"
        "Also write alt text describing what is *visible* in the picture — not what it means."
    ),
)

CARDS = PromptTemplate(
    template_id="copy.cards",
    version="1.0.0",
    purpose="The text on each card of a carousel.",
    text=(
        "Write {count} carousel cards about: {topic}\n"
        "Audience: {audience}. Tone: {tone}.\n"
        "One idea per card, at most {max_chars} characters each. The first card has to make"
        " someone stop; the last one has to be worth having read the others for."
    ),
)

REGISTRY: dict[str, PromptTemplate] = {
    template.template_id: template for template in (CAPTION, CARDS)
}
"""Every template by id. The scriptwriter's prompt is not here yet and that is deliberate: it is
assembled from claim cards, dataset columns and a word budget, so it is a *builder* rather than a
format string, and moving it here as text would mean pretending otherwise. It sends
:data:`SYSTEM_INSTRUCTION`, which is the part that had to be shared."""


def get(template_id: str) -> PromptTemplate:
    template = REGISTRY.get(template_id)
    if template is None:
        known = ", ".join(sorted(REGISTRY))
        msg = f"no prompt template {template_id!r}; known: {known}"
        raise KeyError(msg)
    return template
