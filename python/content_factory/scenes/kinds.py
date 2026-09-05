"""Which scene kinds the renderer actually draws, in one place both languages can check.

The scene grammar declares twenty-one kinds. The Remotion switch draws fourteen of them and sends
the other seven to a labelled placeholder — and a placeholder passes QC, so a plan can name
``ranking`` today and the film ships with a grey card saying "ranking" on it where a chart was
meant to be. Nothing compared the two lists, in either direction.

So the list lives here, `tests/unit/test_scene_kinds.py` asserts it against the actual TypeScript
switch, and two things read it: the script writer, which may not ask for a kind that cannot be
drawn, and `qc_deliverable`, which now fails a deliverable whose plan names one.

Adding a scene is therefore a three-line change with a test that enforces the order: write the
component, add it to the switch, add it here. Doing the first two and forgetting the third leaves a
kind the writer will not use; doing the third alone fails the test.
"""

from __future__ import annotations

IMPLEMENTED_KINDS: frozenset[str] = frozenset(
    {
        "title",
        "section_intro",
        "big_number",
        "bullet_sequence",
        "source_card",
        "outro",
        "callout",
        "quote",
        "definition",
        "chapter_transition",
        "chart",
        "timeline",
        "map",
        "screenshot",
        "image",
        "comparison",
        "flow_diagram",
    }
)
"""Kinds with a real component in ``packages/video-ui/src/TimelineComposition.tsx``."""

PLACEHOLDER_KINDS: frozenset[str] = frozenset(
    {"ranking", "data_table", "relationship_diagram", "manim_asset"}
)
"""Declared in the grammar, drawn as a labelled grey card. A plan may still contain one — an
operator can want the placeholder while a component is being written — but ``qc_deliverable`` says
so, loudly, instead of letting it ship silently."""


ASSET_BACKED_KINDS: frozenset[str] = frozenset({"image", "screenshot", "manim_asset"})
"""Kinds whose content is a file. The script writer may only choose one of these when an asset id
actually exists, because a model inventing an ``asset_id`` produces a scene that renders nothing."""

SOURCE_BACKED_KINDS: frozenset[str] = frozenset({"quote", "screenshot", "source_card"})
"""Kinds that name a ``source_id``. Same rule and a sharper reason: a quote attributed to a source
that does not exist is a fabricated citation."""

DATA_BACKED_KINDS: frozenset[str] = frozenset({"big_number", "chart", "ranking", "data_table"})
"""Kinds that resolve a figure through a ``DataRef``. The writer may only choose one when a
dataset is available, and every number it puts on screen has to be in that dataset's rows."""
