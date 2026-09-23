"""Semantic layout to pixels: regions per template and a verified box for every text entity."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.fonts import FIT_SAFETY, FontFamily, measure_text, wrap_text
from content_factory.explainer.tokens_gen import TOKENS
from content_factory.schemas.explainer import (
    AnnotateAction,
    ChartTemplate,
    DiagramLayout,
    DiagramTemplate,
    Entity,
    EntityBox,
    NamedRegion,
    PixelBox,
    Scene,
    SourceDocumentTemplate,
    TextTemplate,
    VisualSpec,
)

Role = Literal["display", "h1", "h2", "body", "label", "caption"]
_SCALE: dict[str, int] = TOKENS["typography"]["scale_px_at_1080"]
_FLOORS: dict[str, int] = TOKENS["typography"]["floors_px_at_1080"]
LINE_HEIGHT: float = TOKENS["typography"]["line_height"]
CANVAS_WIDTH: int = TOKENS["layout"]["canvas"]["width"]
CANVAS_HEIGHT: int = TOKENS["layout"]["canvas"]["height"]
UNIT: int = TOKENS["layout"]["grid"]["unit"]
GUTTER: int = TOKENS["layout"]["grid"]["gutter"]
CONTENT_BOX = PixelBox(
    x=TOKENS["layout"]["safe_area_px"]["x"],
    y=TOKENS["layout"]["safe_area_px"]["y"],
    width=CANVAS_WIDTH - 2 * TOKENS["layout"]["safe_area_px"]["x"],
    height=CANVAS_HEIGHT - 2 * TOKENS["layout"]["safe_area_px"]["y"],
)
AXIS_Y_WIDTH = 120
AXIS_X_HEIGHT = 56
OVERLAY_FRACTION = 0.25
NODE_HEIGHT = 64
NODE_PADDING = 24
SWATCH = 16
CHIP_PADDING = 12
MAX_SHORT_LABEL = 40
# KaTeX metrics are not in the advance tables; a group is estimated at 0.62 em per character.
FORMULA_EM_PER_CHAR = 0.62
NODE_LABEL_WEIGHT = 600
# The quotation card's chrome around its text (TextTemplate.tsx draws the same card): a UNIT
# accent bar plus 5 UNIT padding on the left, 5 UNIT on the right, 4 UNIT above and below.
CARD_INSET_LEFT = 6 * UNIT
CARD_INSET_RIGHT = 5 * UNIT
CARD_INSET_Y = 4 * UNIT
NO_INSET = (0, 0, 0)
# Ink margin on every side of a text box, so a codec halo under a descender stays inside it; text
# is fitted to the inner width and the renderer centres the line block in the box.
TEXT_PAD = UNIT // 2


@dataclass(frozen=True)
class LayoutChoice:
    """The compiler drew short_label instead of label; the renderer must draw text_used."""

    entity_id: str
    text_used: str


@dataclass(frozen=True)
class SceneBoxes:
    boxes: tuple[EntityBox, ...]
    choices: tuple[LayoutChoice, ...]
    texts: dict[str, str] = field(default_factory=dict)


def role_px(role: Role) -> int:
    px = _SCALE[role]
    floor = _FLOORS["label"] if role in {"label", "caption"} else _FLOORS["read_text"]
    assert px >= floor, f"role {role} is {px} px, below its {floor} px floor"
    return px


def line_px(role: Role) -> int:
    return math.ceil(role_px(role) * LINE_HEIGHT)


def synthetic_id(prefix: str, record_id: str) -> str:
    """An OpaqueId for text that is not an entity (axis titles, annotations), from its record."""
    return f"{prefix}_{record_id.split('_', 1)[1]}"


def regions_for(scene: Scene) -> tuple[NamedRegion, ...]:
    regions = [NamedRegion(name="content", box=CONTENT_BOX)]
    area = template_area(scene)
    if scene.layout == "split":
        regions.append(NamedRegion(name="caption", box=_split(CONTENT_BOX)[1]))
    template = scene.template
    if isinstance(template, ChartTemplate):
        regions.extend(_chart_regions(area))
    elif isinstance(template, DiagramTemplate):
        regions.append(NamedRegion(name="plot", box=area))
    elif isinstance(template, SourceDocumentTemplate):
        regions.append(NamedRegion(name="page", box=_page_region(area)))
    return tuple(regions)


def template_area(scene: Scene) -> PixelBox:
    """The box the scene's template draws in: content, its left half, or the overlay band."""
    if scene.layout == "split":
        return _split(CONTENT_BOX)[0]
    if scene.layout == "overlay":
        band = _grid(int(CONTENT_BOX.height * OVERLAY_FRACTION))
        return PixelBox(
            x=CONTENT_BOX.x,
            y=CONTENT_BOX.y + CONTENT_BOX.height - band,
            width=CONTENT_BOX.width,
            height=band,
        )
    return CONTENT_BOX


def region(scene: Scene, name: str) -> PixelBox:
    for named in regions_for(scene):
        if named.name == name:
            return named.box
    msg = f"scene {scene.scene_id} has no region {name!r}"
    raise KeyError(msg)


def boxes_for(scene: Scene, spec: VisualSpec) -> SceneBoxes:
    """Every text-bearing entity's box at its role size, or a layout issue naming the fix."""
    placer = _placer_for(scene, spec)
    template = scene.template
    if isinstance(template, TextTemplate):
        placer.text(template)
    elif isinstance(template, ChartTemplate):
        placer.chart(template)
    elif isinstance(template, DiagramTemplate):
        placer.diagram(template)
    placer.annotations()
    if placer.issues:
        raise EpisodeInvalidError(placer.issues)
    return SceneBoxes(tuple(placer.boxes), tuple(placer.choices), placer.texts)


def adopt_node_boxes(
    scene: Scene, spec: VisualSpec, placed: SceneBoxes, layout: DiagramLayout
) -> SceneBoxes:
    """Diagram node boxes replaced by ELK's, each re-verified at label size, or a layout issue."""
    placer = _placer_for(scene, spec)
    boxes = placer.adopt(placed.boxes, layout)
    if placer.issues:
        raise EpisodeInvalidError(placer.issues)
    return SceneBoxes(boxes, placed.choices, placed.texts)


def _placer_for(scene: Scene, spec: VisualSpec) -> _Placer:
    index = next(i for i, s in enumerate(spec.scenes) if s.scene_id == scene.scene_id)
    return _Placer(scene, spec, f"VisualSpec.scenes[{index}]")


@dataclass(frozen=True)
class _Placed:
    text: str
    lines: tuple[str, ...]
    width_px: int


class _Placer:
    def __init__(self, scene: Scene, spec: VisualSpec, where: str) -> None:
        self.scene = scene
        self.where = where
        self.entities: dict[str, Entity] = {e.entity_id: e for e in spec.entities}
        self.area = template_area(scene)
        self.boxes: list[EntityBox] = []
        self.choices: list[LayoutChoice] = []
        self.texts: dict[str, str] = {}
        self.issues: list[ContractIssue] = []

    def text(self, template: TextTemplate) -> None:
        items = template.items
        card = template.variant == "quotation_card"
        inset = (CARD_INSET_LEFT, CARD_INSET_RIGHT, CARD_INSET_Y) if card else NO_INSET
        width = self.area.width - inset[0] - inset[1] - 2 * TEXT_PAD
        rows: list[tuple[str, _Placed | None, Role, FontFamily, int]] = []
        for k, item in enumerate(items):
            role, family, weight, max_lines = self._text_role(template.variant, k, len(items))
            if template.variant == "formula":
                est = math.ceil(len(item.text) * FORMULA_EM_PER_CHAR * role_px("body"))
                placed = _Placed(item.text, (item.text,), est)
                self.texts[item.entity_id] = item.text
            else:
                placed = self._place(
                    item.entity_id, (item.text,), width, max_lines, role, family, weight
                )
            rows.append((item.entity_id, placed, role, family, weight))
        if template.variant == "formula":
            self._formula_row(rows)
        else:
            self._stack(rows, UNIT if template.variant == "list" else 2 * UNIT, inset)

    def chart(self, template: ChartTemplate) -> None:
        legend = _chart_regions(self.area)
        by_name = {r.name: r.box for r in legend}
        self._legend(template, by_name["legend"])
        self._axis_titles(template, by_name["axis_x"], by_name["axis_y"])

    def diagram(self, template: DiagramTemplate) -> None:
        plot = self.area
        n = len(template.nodes)
        share = (
            (plot.width - (n - 1) * GUTTER) // n if template.direction == "LR" else plot.width // 2
        )
        for i, node in enumerate(template.nodes):
            entity = self.entities[node.entity_id]
            candidates = (node.label, entity.short_label) if entity.short_label else (node.label,)
            placed = self._place(
                node.entity_id,
                candidates,
                share - 2 * NODE_PADDING,
                1,
                "label",
                "Inter",
                NODE_LABEL_WEIGHT,
            )
            if placed is None:
                continue
            width = placed.width_px + 2 * NODE_PADDING
            if template.direction == "LR":
                x, y = plot.x + i * (share + GUTTER), plot.y + (plot.height - NODE_HEIGHT) // 2
            else:
                x, y = plot.x + (plot.width - width) // 2, plot.y + i * (NODE_HEIGHT + GUTTER)
            self._box(node.entity_id, x, y, width, NODE_HEIGHT, "label", 1)
        for edge in template.edges:
            if not edge.label:
                continue
            placed = self._place(
                edge.entity_id, (edge.label,), plot.width // 2, 1, "label", "Inter", 500
            )
            if placed is not None:
                width, height = placed.width_px + 2 * UNIT, line_px("label") + 2 * TEXT_PAD
                self._box(edge.entity_id, plot.x, plot.y, width, height, "label", 1)

    def annotations(self) -> None:
        # The box carries the verified size; the renderer places it near the target entity.
        plot = self.area
        for beat in self.scene.beats:
            for k, action in enumerate(beat.actions):
                if not isinstance(action, AnnotateAction):
                    continue
                entity_id = synthetic_id(f"annot{k}", beat.beat_id)
                placed = self._place(
                    entity_id, (action.text,), plot.width // 2, 1, "label", "Inter", 500
                )
                if placed is not None:
                    width, height = placed.width_px + 2 * UNIT, line_px("label") + 2 * TEXT_PAD
                    self._box(entity_id, plot.x + UNIT, plot.y + UNIT, width, height, "label", 1)

    def adopt(self, boxes: tuple[EntityBox, ...], layout: DiagramLayout) -> tuple[EntityBox, ...]:
        """Node boxes from ELK, kept only where the compiled text still fits at label size."""
        nodes = {n.entity_id: n.box for n in layout.nodes}
        adopted: list[EntityBox] = []
        for box in boxes:
            node = nodes.get(box.entity_id)
            if node is None:
                adopted.append(box)
                continue
            text = box.text or ""
            measured = FIT_SAFETY * measure_text(
                text, family="Inter", weight=NODE_LABEL_WEIGHT, font_px=role_px("label")
            )
            # ELK sized the node for its label plus 6 UNIT, then may have shrunk the graph to the
            # region; the label must keep a UNIT each side or the scene has to split.
            available = node.width - 2 * UNIT
            if measured > available:
                self._issue(box.entity_id, text, measured, available, 1, 1, "label")
            else:
                adopted.append(box.model_copy(update={"box": node}))
        return tuple(adopted)

    def _text_role(self, variant: str, k: int, count: int) -> tuple[Role, FontFamily, int, int]:
        if variant == "big_number":
            return ("display", "Sora", 800, 1) if k == 0 else ("label", "Inter", 500, 2)
        if variant == "list":
            return "body", "Inter", 400, 1
        if variant == "quotation_card":
            if k == 0:
                return "body", "Inter", 400, 4
            if k == count - 1:
                return "caption", "Inter", 400, 1
            return "body", "Inter", 400, 2
        if variant == "formula":
            return "body", "Inter", 400, 1
        return "body", "Inter", 400, 2

    def _stack(
        self,
        rows: list[tuple[str, _Placed | None, Role, FontFamily, int]],
        gap: int,
        inset: tuple[int, int, int],
    ) -> None:
        left, right, pad_y = inset
        heights = [
            len(p.lines) * line_px(role) + 2 * TEXT_PAD if p else 0 for _, p, role, _, _ in rows
        ]
        total = sum(heights) + gap * (len(rows) - 1)
        if total + 2 * pad_y > self.area.height:
            first = rows[0][0]
            self._height_issue(first, total + 2 * pad_y, self.area.height)
            return
        x, width = self.area.x + left, self.area.width - left - right
        y = self.area.y + (self.area.height - total) // 2
        for (entity_id, placed, role, _, _), height in zip(rows, heights, strict=True):
            if placed is not None:
                self._box(entity_id, x, y, width, height, role, len(placed.lines))
            y += height + gap

    def _formula_row(self, rows: list[tuple[str, _Placed | None, Role, FontFamily, int]]) -> None:
        height = 2 * line_px("body") + 2 * TEXT_PAD
        widths = [p.width_px + 2 * TEXT_PAD if p else 0 for _, p, _, _, _ in rows]
        total = sum(widths) + 2 * UNIT * (len(rows) - 1)
        if total > self.area.width:
            widest = max(rows, key=lambda r: r[1].width_px if r[1] else 0)
            assert widest[1] is not None
            self._issue(
                widest[0], widest[1].text, widest[1].width_px, self.area.width, 1, 1, "body"
            )
            return
        x = self.area.x + (self.area.width - total) // 2
        y = self.area.y + (self.area.height - height) // 2
        for (entity_id, _, _, _, _), width in zip(rows, widths, strict=True):
            self._box(entity_id, x, y, width, height, "body", 1)
            x += width + 2 * UNIT

    def _legend(self, template: ChartTemplate, legend: PixelBox) -> None:
        n = len(template.series)
        chip_max = min((legend.width - (n - 1) * GUTTER) // n, legend.width // 2)
        chrome = SWATCH + UNIT + 2 * CHIP_PADDING + 2 * TEXT_PAD
        height = line_px("label") + 2 * TEXT_PAD
        x = legend.x
        y = legend.y + (legend.height - height) // 2
        for binding in template.series:
            entity = self.entities[binding.entity_id]
            candidates = (
                (entity.label, entity.short_label) if entity.short_label else (entity.label,)
            )
            placed = self._place(
                binding.entity_id, candidates, chip_max - chrome, 1, "label", "Inter", 500
            )
            if placed is None:
                continue
            width = placed.width_px + chrome
            self._box(binding.entity_id, x, y, width, height, "label", 1)
            x += width + GUTTER

    def _axis_titles(self, template: ChartTemplate, axis_x: PixelBox, axis_y: PixelBox) -> None:
        line = line_px("label") + 2 * TEXT_PAD
        if template.x.title:
            entity_id = synthetic_id("axisx", self.scene.scene_id)
            placed = self._place(
                entity_id,
                (template.x.title,),
                axis_x.width - 2 * TEXT_PAD,
                1,
                "label",
                "Inter",
                500,
            )
            if placed is not None:
                width = placed.width_px + 2 * TEXT_PAD
                x = axis_x.x + (axis_x.width - width) // 2
                y = axis_x.y + (axis_x.height - line) // 2
                self._box(entity_id, x, y, width, line, "label", 1)
        if template.y.title:
            entity_id = synthetic_id("axisy", self.scene.scene_id)
            placed = self._place(
                entity_id,
                (template.y.title,),
                axis_y.height - 2 * TEXT_PAD,
                1,
                "label",
                "Inter",
                500,
            )
            if placed is not None:
                # Drawn rotated: the box is as tall as the text is long.
                height = placed.width_px + 2 * TEXT_PAD
                y = axis_y.y + (axis_y.height - height) // 2
                self._box(entity_id, axis_y.x, y, line, height, "label", 1)

    def _place(
        self,
        entity_id: str,
        candidates: tuple[str, ...],
        width_px: int,
        max_lines: int,
        role: Role,
        family: FontFamily,
        weight: int,
    ) -> _Placed | None:
        font_px = role_px(role)
        for k, text in enumerate(candidates):
            lines = wrap_text(text, width_px, family=family, weight=weight, font_px=font_px)
            widest = max(
                measure_text(s, family=family, weight=weight, font_px=font_px) for s in lines
            )
            if len(lines) <= max_lines and widest * FIT_SAFETY <= width_px:
                if k > 0:
                    self.choices.append(LayoutChoice(entity_id, text))
                self.texts[entity_id] = text
                return _Placed(text, tuple(lines), math.ceil(widest * FIT_SAFETY))
        text = candidates[0]
        single = measure_text(text, family=family, weight=weight, font_px=font_px) * FIT_SAFETY
        needed = len(wrap_text(text, width_px, family=family, weight=weight, font_px=font_px))
        self._issue(entity_id, text, single, width_px, needed, max_lines, role)
        return None

    def _box(
        self, entity_id: str, x: int, y: int, width: int, height: int, role: Role, lines: int
    ) -> None:
        box = PixelBox(x=x, y=y, width=max(1, width), height=max(1, height))
        self.boxes.append(
            EntityBox(
                entity_id=entity_id,
                box=box,
                font_px=role_px(role),
                lines=lines,
                text=self.texts.get(entity_id),
            )
        )

    def _issue(
        self,
        entity_id: str,
        text: str,
        measured: float,
        available: int,
        needed: int,
        allowed: int,
        role: Role,
    ) -> None:
        chars = max(
            1, min(MAX_SHORT_LABEL, int(len(text) * available * allowed / max(measured, 1.0)))
        )
        self.issues.append(
            ContractIssue(
                kind="layout",
                where=f"{self.where} entity {entity_id}",
                message=(
                    f"scene {self.scene.scene_id}: {text!r} of entity {entity_id} measures "
                    f"{measured:.0f} px at {role} size ({role_px(role)} px) but {available} px are "
                    f"available; {needed} lines needed, {allowed} allowed."
                ),
                fix=self._fix(entity_id, chars),
                ids=(self.scene.scene_id, entity_id),
            )
        )

    def _height_issue(self, entity_id: str, total: int, available: int) -> None:
        self.issues.append(
            ContractIssue(
                kind="layout",
                where=f"{self.where}.template.items",
                message=(
                    f"scene {self.scene.scene_id}: the text items stack to {total} px but the "
                    f"region is {available} px tall."
                ),
                fix=self._fix(entity_id, MAX_SHORT_LABEL),
                ids=(self.scene.scene_id, entity_id),
            )
        )

    def _fix(self, entity_id: str, chars: int) -> str:
        beat_id = self.scene.beats[0].beat_id
        for beat in self.scene.beats:
            for action in beat.actions:
                if action.action == "reveal" and entity_id in getattr(action, "targets", ()):
                    beat_id = beat.beat_id
                    break
        return (
            f"give entity {entity_id} a short_label of at most {chars} characters, or split "
            f"scene {self.scene.scene_id} after beat {beat_id}, or use layout 'split'"
        )


def _page_region(area: PixelBox) -> PixelBox:
    """The area minus a label-high attribution bar below it, so the source stays named on screen."""
    bar = _grid_up(line_px("label")) + UNIT
    return PixelBox(x=area.x, y=area.y, width=area.width, height=area.height - bar)


def _split(box: PixelBox) -> tuple[PixelBox, PixelBox]:
    left_w = _grid((box.width - GUTTER) // 2)
    left = PixelBox(x=box.x, y=box.y, width=left_w, height=box.height)
    right_x = box.x + left_w + GUTTER
    right = PixelBox(x=right_x, y=box.y, width=box.x + box.width - right_x, height=box.height)
    return left, right


def _chart_regions(area: PixelBox) -> list[NamedRegion]:
    title_h = _grid_up(line_px("h2"))
    legend_h = _grid_up(line_px("label") + 2 * UNIT)
    body_y = area.y + title_h + 2 * UNIT
    body_h = area.height - title_h - legend_h - 4 * UNIT
    plot_h = body_h - AXIS_X_HEIGHT
    plot_x = area.x + AXIS_Y_WIDTH
    plot_w = area.width - AXIS_Y_WIDTH
    return [
        NamedRegion(
            name="title", box=PixelBox(x=area.x, y=area.y, width=area.width, height=title_h)
        ),
        NamedRegion(name="plot", box=PixelBox(x=plot_x, y=body_y, width=plot_w, height=plot_h)),
        NamedRegion(
            name="axis_y", box=PixelBox(x=area.x, y=body_y, width=AXIS_Y_WIDTH, height=plot_h)
        ),
        NamedRegion(
            name="axis_x",
            box=PixelBox(x=plot_x, y=body_y + plot_h, width=plot_w, height=AXIS_X_HEIGHT),
        ),
        NamedRegion(
            name="legend",
            box=PixelBox(
                x=area.x, y=area.y + area.height - legend_h, width=area.width, height=legend_h
            ),
        ),
    ]


def _grid(value: int) -> int:
    return max(UNIT, value - value % UNIT)


def _grid_up(value: int) -> int:
    return math.ceil(value / UNIT) * UNIT
