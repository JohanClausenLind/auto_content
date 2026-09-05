"""The scene grammar's two halves have to agree: Python says what is drawable, TypeScript draws it.

``IMPLEMENTED_KINDS`` gates real behaviour on the Python side — the script writer refuses a beat
whose kind is not in it, and ``qc_deliverable`` fails a plan that uses one that is not. The list
is therefore a claim about code that lives in another language, in another package, and nothing
checked it. It was wrong within an hour of being written: `image`, `comparison` and `flow_diagram`
went in as implemented while their components did not exist yet, so the writer would have been
free to plan three kinds that rendered as grey placeholder cards and passed QC doing it.

These tests read the TypeScript. Both files, because they can disagree with each other too: the
switch is what actually runs, and `mapping.ts`'s list is what the renderer's own callers ask.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from content_factory.scenes.kinds import IMPLEMENTED_KINDS, PLACEHOLDER_KINDS
from content_factory.schemas.scenes import SceneSpec

REPO = Path(__file__).resolve().parents[2]
MAPPING = REPO / "packages/video-ui/src/mapping.ts"
SWITCH = REPO / "packages/video-ui/src/TimelineComposition.tsx"


def _ts_implemented_kinds() -> set[str]:
    """The `IMPLEMENTED_KINDS` array literal in `mapping.ts`."""
    body = re.search(
        r"export const IMPLEMENTED_KINDS = \[(.*?)\] as const", MAPPING.read_text(), re.S
    )
    assert body is not None, "mapping.ts no longer declares IMPLEMENTED_KINDS as an array literal"
    return set(re.findall(r'"([a-z_]+)"', body.group(1)))


def _switch_cases() -> set[str]:
    """The kinds `SceneSwitch` sends to a real component (its `default` is the placeholder)."""
    text = SWITCH.read_text()
    body = re.search(r"export function SceneSwitch\((.*?)\n}\n", text, re.S)
    assert body is not None, "TimelineComposition.tsx no longer exports a SceneSwitch function"
    return set(re.findall(r'case "([a-z_]+)":', body.group(1)))


def _all_scene_kinds() -> set[str]:
    """Every `kind` the SceneSpec union admits."""
    return {
        member.model_fields["kind"].default
        for member in SceneSpec.__origin__.__args__  # type: ignore[attr-defined]
    }


def test_python_and_typescript_agree_on_what_is_implemented() -> None:
    assert _ts_implemented_kinds() == set(IMPLEMENTED_KINDS)


def test_every_implemented_kind_has_a_case_in_the_switch() -> None:
    """The switch is the only thing that decides what renders; the lists are documentation."""
    missing = sorted(set(IMPLEMENTED_KINDS) - _switch_cases())
    assert missing == [], f"claimed implemented but falls through to the placeholder: {missing}"


def test_no_kind_is_drawn_without_being_declared_implemented() -> None:
    """The other direction: a component wired up but left out of the list is a scene the writer
    is refused permission to plan, which is a silent capability loss rather than a broken film."""
    extra = sorted(_switch_cases() - set(IMPLEMENTED_KINDS))
    assert extra == [], f"drawn by the switch but not declared implemented: {extra}"


def test_the_two_kind_sets_partition_the_scene_grammar() -> None:
    """Every kind in the contract is either drawable or knowingly a placeholder — no third state
    where a kind exists, nothing draws it, and nothing says so."""
    known = set(IMPLEMENTED_KINDS) | set(PLACEHOLDER_KINDS)
    assert _all_scene_kinds() == known
    assert not (set(IMPLEMENTED_KINDS) & set(PLACEHOLDER_KINDS))


@pytest.mark.parametrize("kind", ["image", "comparison", "flow_diagram"])
def test_the_three_kinds_this_pass_added_are_wired_end_to_end(kind: str) -> None:
    assert kind in IMPLEMENTED_KINDS
    assert kind in _ts_implemented_kinds()
    assert kind in _switch_cases()
