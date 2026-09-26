"""Entity colour allocation: one token per entity for the whole episode, checked per scene."""

from __future__ import annotations

from itertools import combinations

from content_factory.explainer.color import Oklch, delta_e_ok, hex_of, oklch_to_srgb
from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.tokens_gen import TOKENS
from content_factory.schemas.explainer import (
    ChartTemplate,
    DiagramTemplate,
    EntityColor,
    Scene,
    TextTemplate,
    VisualSpec,
)

CATEGORICAL_IDS: tuple[str, ...] = tuple(t["id"] for t in TOKENS["color"]["data"]["categorical"])
CATEGORICAL_KINDS = frozenset({"series", "node"})
INK_ID = "ui.ink.primary"
STROKE_ID = "ui.stroke.strong"
REGION_ID = "ui.surface.2"
MAX_CO_VISIBLE = 6
# Below this OKLab distance two co-visible entities read as one colour (color.delta_e_ok).
MIN_DELTA_E = 0.08
_OKLCH: dict[str, Oklch] = {
    t["id"]: Oklch(*t["oklch"])
    for group in (
        TOKENS["color"]["ui"],
        TOKENS["color"]["data"]["categorical"],
        TOKENS["color"]["state"],
    )
    for t in group
    if "oklch" in t
}


def token_oklch(token_id: str) -> Oklch:
    return _OKLCH[token_id]


def token_hex(token_id: str) -> str:
    return hex_of(oklch_to_srgb(_OKLCH[token_id]))


def bind_categorical(spec: VisualSpec) -> dict[str, str]:
    """Categorical tokens by order of first appearance across the episode; extras cycle."""
    kinds = {e.entity_id: e.kind for e in spec.entities}
    order: list[str] = []
    seen: set[str] = set()
    appearances = [eid for scene in spec.scenes for eid in scene_entities(scene)]
    for eid in [*appearances, *(e.entity_id for e in spec.entities)]:
        if eid not in seen and kinds.get(eid) in CATEGORICAL_KINDS:
            seen.add(eid)
            order.append(eid)
    return {eid: CATEGORICAL_IDS[i % len(CATEGORICAL_IDS)] for i, eid in enumerate(order)}


def scene_entities(scene: Scene) -> tuple[str, ...]:
    """Entities a scene can show, in appearance order: initial, template-bound, then targeted."""
    ids: list[str] = list(scene.initial_visible)
    template = scene.template
    if isinstance(template, ChartTemplate):
        ids.extend(s.entity_id for s in template.series)
    elif isinstance(template, DiagramTemplate):
        ids.extend(n.entity_id for n in template.nodes)
        ids.extend(e.entity_id for e in template.edges)
    elif isinstance(template, TextTemplate):
        ids.extend(i.entity_id for i in template.items)
    for beat in scene.beats:
        for action in beat.actions:
            ids.extend(getattr(action, "targets", ()))
    return tuple(dict.fromkeys(ids))


def co_visible_sets(scene: Scene) -> tuple[tuple[str, ...], ...]:
    """Per beat, what is on screen at once; a hide takes effect only after its beat animates."""
    visible: dict[str, None] = dict.fromkeys(scene.initial_visible)
    sets = [tuple(visible)]
    for beat in scene.beats:
        hidden: list[str] = []
        for action in beat.actions:
            targets = getattr(action, "targets", ())
            if action.action == "reveal":
                visible.update(dict.fromkeys(targets))
            elif action.action == "hide":
                hidden.extend(targets)
        sets.append(tuple(visible))
        for eid in hidden:
            visible.pop(eid, None)
    return tuple(sets)


def allocate_colors(spec: VisualSpec) -> dict[str, tuple[EntityColor, ...]]:
    """Colours per scene from the episode-wide binding, or a colour issue naming the pair."""
    binding = bind_categorical(spec)
    kinds = {e.entity_id: e.kind for e in spec.entities}
    issues: list[ContractIssue] = []
    result: dict[str, tuple[EntityColor, ...]] = {}
    for i, scene in enumerate(spec.scenes):
        colors: list[EntityColor] = []
        for eid in scene_entities(scene):
            token = _token_for(eid, kinds[eid], binding)
            if token is not None:
                colors.append(EntityColor(entity_id=eid, token_id=token, srgb_hex=token_hex(token)))
        result[scene.scene_id] = tuple(colors)
        issues += _co_visibility_issues(f"VisualSpec.scenes[{i}]", scene, kinds, binding)
    if issues:
        raise EpisodeInvalidError(issues)
    return result


def _token_for(entity_id: str, kind: str, binding: dict[str, str]) -> str | None:
    if kind in CATEGORICAL_KINDS:
        return binding[entity_id]
    if kind == "region":
        return REGION_ID
    if kind == "edge":
        return STROKE_ID
    return INK_ID


def _co_visibility_issues(
    where: str, scene: Scene, kinds: dict[str, str], binding: dict[str, str]
) -> list[ContractIssue]:
    fix = "hide one before revealing the other, or merge the series."
    largest = max(
        (
            [eid for eid in group if kinds[eid] in CATEGORICAL_KINDS]
            for group in co_visible_sets(scene)
        ),
        key=len,
    )
    if len(largest) > MAX_CO_VISIBLE:
        return [
            ContractIssue(
                kind="color",
                where=where,
                message=(
                    f"scene {scene.scene_id} can show {len(largest)} categorical entities at once "
                    f"({', '.join(largest)}); the palette holds {MAX_CO_VISIBLE}."
                ),
                fix=fix,
                ids=(scene.scene_id, *largest),
            )
        ]
    issues: list[ContractIssue] = []
    pairs = {
        pair
        for group in co_visible_sets(scene)
        for pair in combinations([e for e in group if kinds[e] in CATEGORICAL_KINDS], 2)
    }
    for a, b in sorted(pairs):
        distance = delta_e_ok(token_oklch(binding[a]), token_oklch(binding[b]))
        if distance < MIN_DELTA_E:
            issues.append(
                ContractIssue(
                    kind="color",
                    where=where,
                    message=(
                        f"scene {scene.scene_id} shows {a} ({binding[a]}) and {b} ({binding[b]}) "
                        f"together at OKLab distance {distance:.3f}, below {MIN_DELTA_E}."
                    ),
                    fix=fix,
                    ids=(scene.scene_id, a, b),
                )
            )
    return issues
