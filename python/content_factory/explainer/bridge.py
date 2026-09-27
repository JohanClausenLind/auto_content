"""An explainer plan as today's StoryPlan: one typeset scene per beat, drawn by today's renderer.

The explainer plan describes one evolving diagram: persistent objects, zooms between layers, and
operations anchored to sentences. The renderer that draws that does not exist yet; the Remotion
timeline draws one scene per beat with hard cuts. So this is a **bridge**, and it is lossy on
purpose, in exactly these ways:

* each beat becomes one scene, so zooms, morphs and traces become cuts between diagrams;
* a mechanism beat becomes a ``flow_diagram`` of what is on screen inside its frame at the end of
  the beat, with the arrows between those objects as edges; the beat's own targets come first
  when there are more than twelve;
* fault marks, highlights and cell indices are not drawn;
* the four framing beats use the card that says their one line: the title question, the common
  assumption, the answer and the takeaway.

What it keeps exactly is the narration: every beat's sentences become the beat's spoken text, so
the voice, the measured timings and the captions are the ones the plan wrote. When the scene-graph
renderer lands it reads the ExplainerPlan (``story/explainer.json``) directly and this goes away.
"""

from __future__ import annotations

from typing import Any

from content_factory.schemas import scenes as sc
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.explainer import ExplainerBeat, ExplainerPlan

BRIDGE_VERSION = "0.1.0"

SECTION_FOR: dict[str, str] = {
    "question_hook": "cold_open",
    "common_assumption": "question_stakes",
    "system_overview": "build_model",
    "normal_flow": "run_system",
    "layer_zoom": "run_system",
    "comparison": "change_variable",
    "edge_case": "show_limits",
    "exception_path": "show_limits",
    "resolution": "synthesis",
    "takeaway": "synthesis",
}
"""Beat type to the documentary arc's section (``EpisodeSectionKind``), so the shorts planner and
anything else that asks "which beat is the cold open" still gets an answer."""

MAX_NODES = 12  # FlowDiagramScene's own limit
NODE_LABEL_CHARS = 60
# Objects that are part of an arrow's meaning or float over the diagram, not boxes in a flow.
NOT_NODES = frozenset({"arrow", "data_packet", "fault_marker", "callout", "label"})


def _text(value: str) -> sc.TextRef:
    return sc.TextRef(text=value.strip()[:2000] or "-")


def on_screen_by_beat(plan: ExplainerPlan) -> list[set[str]]:
    """The objects on screen at the end of each beat, following the prompt's entrance rules.

    A simplified replay of what ``explainer.check`` validates: a checked plan never targets an
    object that is not on screen, so this only has to track entrances and exits.
    """
    parent = {o.id: o.parent for o in plan.objects}
    visible: set[str] = set()
    out: list[set[str]] = []
    for beat in plan.beats:
        visible.add(beat.frame)
        for u in beat.visual_updates:
            targets = list(u.targets)
            if u.op == "reveal":
                visible.update(targets)
            elif u.op in {"split", "branch"}:
                visible.update(targets[1:])
            elif u.op == "trace":
                visible.add(targets[0])
            elif u.op in {"morph", "merge"}:
                gone = set(targets)
                for oid in list(visible):
                    up, seen = parent.get(oid, ""), {oid}
                    while up and up not in seen:
                        if up in gone:
                            visible.discard(oid)
                            break
                        seen.add(up)
                        up = parent.get(up, "")
                visible.difference_update(gone)
                if u.into:
                    visible.add(u.into)
            elif u.op == "dismiss":
                visible.difference_update(targets)
        out.append(set(visible))
    return out


def _descendants(frame: str, parent: dict[str, str]) -> set[str]:
    out: set[str] = set()
    for oid in parent:
        up, seen = parent[oid], {oid}
        while up and up not in seen:
            if up == frame:
                out.add(oid)
                break
            seen.add(up)
            up = parent.get(up, "")
    return out


def _scene(
    plan: ExplainerPlan, beat: ExplainerBeat, on_screen: set[str], *, scene_id: str, beat_id: str
) -> sc.SceneSpec:
    # Annotated: each branch spreads this into a different scene model (see scriptwriter).
    common: dict[str, Any] = {"scene_id": scene_id, "beat_id": beat_id}
    if beat.type == "question_hook":
        return sc.TitleScene(**common, title=_text(plan.title_question))
    if beat.type == "common_assumption":
        return sc.CalloutScene(**common, text=_text(plan.common_assumption), tone="neutral")
    if beat.type == "resolution":
        return sc.CalloutScene(**common, text=_text(plan.answer), tone="positive")
    if beat.type == "takeaway":
        return sc.OutroScene(**common, text=_text(plan.takeaway))

    objects = {o.id: o for o in plan.objects}
    compare = next((u for u in beat.visual_updates if u.op == "compare"), None)
    if compare is not None:
        left, right = (objects[t].label or t for t in compare.targets[:2])
        return sc.ComparisonScene(
            **common, left=_text(left), right=_text(right), title=_text(beat.question)
        )

    parent = {o.id: o.parent for o in plan.objects}
    inside = _descendants(beat.frame, parent) & on_screen
    candidates = [oid for oid in inside if objects[oid].primitive not in NOT_NODES]
    targeted = [t for u in beat.visual_updates for t in (*u.targets, u.into) if t]
    order = {oid: i for i, oid in enumerate(o.id for o in plan.objects)}
    first = [oid for oid in dict.fromkeys(targeted) if oid in candidates]
    rest = sorted((oid for oid in candidates if oid not in first), key=order.__getitem__)
    nodes = (first + rest)[:MAX_NODES]
    if len(nodes) >= 2:
        chosen = set(nodes)
        edges = tuple(
            sc.DiagramEdge(from_id=o.from_[:40], to_id=o.to[:40])
            for o in plan.objects
            if o.primitive == "arrow" and o.from_ in chosen and o.to in chosen and o.id in on_screen
        )
        return sc.FlowDiagramScene(
            **common,
            nodes=tuple(
                sc.DiagramNode(
                    node_id=oid[:40],
                    # A data object's label is its cells; " | " is the wire separator, not text.
                    label=_text(
                        (objects[oid].label or oid).replace(" | ", " · ")[:NODE_LABEL_CHARS]
                    ),
                )
                for oid in nodes
            ),
            edges=edges,
            title=_text(beat.question),
        )
    # Too little on screen to draw a flow: say the beat's causal claim instead.
    tone = "warning" if beat.type in {"edge_case", "exception_path"} else "neutral"
    return sc.CalloutScene(**common, text=_text(beat.mechanism or beat.question), tone=tone)


def story_plan_from_explainer(
    plan: ExplainerPlan,
    *,
    deliverable_id: str,
    width: int,
    height: int,
    fps: int = 30,
) -> sc.StoryPlan:
    """One VisualBeat and one scene per explainer beat, narration kept word for word."""
    digest = sha256_hex(
        (deliverable_id + BRIDGE_VERSION + plan.model_dump_json(by_alias=True)).encode()
    )
    screens = on_screen_by_beat(plan)
    beats: list[sc.VisualBeat] = []
    built: list[sc.SceneSpec] = []
    for index, (beat, on_screen) in enumerate(zip(plan.beats, screens, strict=True)):
        text = " ".join(s.strip() for s in beat.narration)
        if len(text) > 1000:
            msg = f"{beat.id}: narration is {len(text)} characters; a beat holds at most 1000"
            raise ValueError(msg)
        beat_id = f"bet_{sha256_hex(f'{digest}{index}'.encode())[:12]}"
        beats.append(
            sc.VisualBeat(
                beat_id=beat_id,
                order=index,
                display_text=text,
                section=SECTION_FOR[beat.type],
                planned_duration_ms=max(200, beat.est_seconds * 1000),
            )
        )
        built.append(
            _scene(
                plan,
                beat,
                on_screen,
                scene_id=f"scn_{beat_id.removeprefix('bet_')}",
                beat_id=beat_id,
            )
        )
    return sc.StoryPlan(
        plan_id=f"stp_{digest[:12]}",
        deliverable_id=deliverable_id,
        fps=fps,  # type: ignore[arg-type]
        width=width,
        height=height,
        beats=tuple(beats),
        scenes=tuple(built),
        hook_text=plan.title_question[:120] or None,
    )
