"""Build fixtures/explainer/render/bundle-min.json from the models so it stays valid."""

# ruff: noqa: S106 — token_id is a colour token, not a credential

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from content_factory.schemas.explainer import (
    AnnotateAction,
    AssetRef,
    Beat,
    ChartTemplate,
    CompiledExplainerScene,
    Cue,
    DatasetColumn,
    DatasetRow,
    DiagramEdgeSpec,
    DiagramLayout,
    DiagramNodeSpec,
    DiagramTemplate,
    Entity,
    EntityBox,
    EntityColor,
    EvidenceDataset,
    ExplainerRenderBundle,
    ExplainerTimeline,
    FieldEncoding,
    LayoutEdge,
    LayoutNode,
    LayoutPoint,
    NamedRegion,
    PixelBox,
    ResolvedAction,
    Scene,
    SeriesBinding,
    TargetAction,
    TextItem,
    TextTemplate,
    VisualSpec,
)

OUT = Path(sys.argv[1])
FPS = 30
WIND, SOLAR = "ent_wind0001", "ent_solar001"
NUM, NUMLBL = "ent_num00001", "ent_numlbl01"
SUN, PANEL, GRID = "ent_nsun0001", "ent_npanel01", "ent_ngrid001"
EDC, EAC = "ent_edc00001", "ent_eac00001"
BARS, NUMBER, FLOW = "scn_bars0001", "scn_num00001", "scn_flow0001"
HEX = {"blue": "#0172B2", "orange": "#E69F00", "green": "#009E73"}


def box(x: int, y: int, w: int, h: int) -> PixelBox:
    return PixelBox(x=x, y=y, width=w, height=h)


def cue(segment: str, start: int, end: int) -> Cue:
    return Cue(segment_id=segment, token_start=start, token_end=end)


def resolved(beat: str, action: str, start: int, end: int, *targets: str) -> ResolvedAction:
    return ResolvedAction(
        beat_id=beat, index=0, action=action, start_frame=start, end_frame=end, targets=targets
    )


dataset = EvidenceDataset(
    dataset_id="ds_energy001",
    title="Installed wind and solar capacity",
    columns=(
        DatasetColumn(name="year", kind="temporal"),
        DatasetColumn(name="wind", kind="quantitative", unit="GW"),
        DatasetColumn(name="solar", kind="quantitative", unit="GW"),
    ),
    rows=(
        DatasetRow(key="2021", values=("2021", 60.0, 70.0)),
        DatasetRow(key="2022", values=("2022", 68.0, 88.0)),
        DatasetRow(key="2023", values=("2023", 75.0, 104.0)),
        DatasetRow(key="2024", values=("2024", 84.0, 124.0)),
    ),
)

spec = VisualSpec(
    spec_id="spec_min00001",
    script_id="script_min00001",
    script_hash=hashlib.sha256(b"script fixture").hexdigest(),
    pack_hash=hashlib.sha256(b"pack fixture").hexdigest(),
    design_system_version=1,
    entities=(
        Entity(entity_id=WIND, label="Wind", short_label="Wind", kind="series"),
        Entity(entity_id=SOLAR, label="Solar", short_label="Solar", kind="series"),
        Entity(entity_id=NUM, label="Installed solar capacity", kind="value"),
        Entity(entity_id=NUMLBL, label="installed solar capacity, 2024", kind="text"),
        Entity(entity_id=SUN, label="Sunlight", kind="node"),
        Entity(entity_id=PANEL, label="Solar panel", kind="node"),
        Entity(entity_id=GRID, label="Grid", kind="node"),
        Entity(entity_id=EDC, label="DC", kind="edge"),
        Entity(entity_id=EAC, label="AC", kind="edge"),
    ),
    assets=(
        AssetRef(
            asset_id="ast_dsenergy01",
            kind="dataset",
            sha256=dataset.content_hash(),
            dataset_id=dataset.dataset_id,
        ),
    ),
    scenes=(
        Scene(
            scene_id=BARS,
            section="build_model",
            purpose="Stack wind and solar capacity by year, then point at solar in 2024",
            template=ChartTemplate(
                template="chart",
                chart_kind="stacked_bar",
                dataset_asset_id="ast_dsenergy01",
                x=FieldEncoding(field="year", kind="temporal", title="Year"),
                y=FieldEncoding(field="wind", kind="quantitative", unit="GW", title="Capacity"),
                series=(
                    SeriesBinding(value="wind", entity_id=WIND),
                    SeriesBinding(value="solar", entity_id=SOLAR),
                ),
            ),
            beats=(
                Beat(
                    beat_id="beat_barwind01",
                    cue=cue("seg_build0001", 0, 2),
                    actions=(TargetAction(action="reveal", targets=(WIND,)),),
                ),
                Beat(
                    beat_id="beat_barsolar1",
                    cue=cue("seg_build0001", 3, 5),
                    actions=(TargetAction(action="reveal", targets=(SOLAR,)),),
                ),
                Beat(
                    beat_id="beat_barhilit1",
                    cue=cue("seg_build0001", 6, 8),
                    actions=(TargetAction(action="highlight", targets=(SOLAR,)),),
                ),
                Beat(
                    beat_id="beat_barannot1",
                    cue=cue("seg_build0001", 9, 12),
                    actions=(
                        AnnotateAction(
                            action="annotate", targets=(SOLAR,), text="124 GW of solar in 2024"
                        ),
                    ),
                ),
                Beat(
                    beat_id="beat_barclear1",
                    cue=cue("seg_build0001", 13, 14),
                    actions=(TargetAction(action="clear_highlight"),),
                ),
            ),
        ),
        Scene(
            scene_id=NUMBER,
            section="question_stakes",
            purpose="The one number the episode turns on",
            template=TextTemplate(
                template="text",
                variant="big_number",
                items=(
                    TextItem(entity_id=NUM, text="124 GW"),
                    TextItem(entity_id=NUMLBL, text="installed solar capacity, 2024"),
                ),
            ),
            beats=(
                Beat(
                    beat_id="beat_numshow01",
                    cue=cue("seg_stakes001", 0, 1),
                    actions=(TargetAction(action="reveal", targets=(NUM,)),),
                ),
                Beat(
                    beat_id="beat_numlabel1",
                    cue=cue("seg_stakes001", 2, 4),
                    actions=(TargetAction(action="reveal", targets=(NUMLBL,)),),
                ),
            ),
        ),
        Scene(
            scene_id=FLOW,
            section="run_system",
            purpose="Sunlight becomes DC in the panel and AC on the grid",
            template=DiagramTemplate(
                template="diagram",
                direction="LR",
                nodes=(
                    DiagramNodeSpec(entity_id=SUN, label="Sunlight"),
                    DiagramNodeSpec(entity_id=PANEL, label="Solar panel"),
                    DiagramNodeSpec(entity_id=GRID, label="Grid"),
                ),
                edges=(
                    DiagramEdgeSpec(
                        entity_id=EDC, source_entity_id=SUN, target_entity_id=PANEL, label="DC"
                    ),
                    DiagramEdgeSpec(
                        entity_id=EAC, source_entity_id=PANEL, target_entity_id=GRID, label="AC"
                    ),
                ),
            ),
            beats=(
                Beat(
                    beat_id="beat_flownodes1",
                    cue=cue("seg_system001", 0, 2),
                    actions=(TargetAction(action="reveal", targets=(SUN, PANEL, GRID)),),
                ),
                Beat(
                    beat_id="beat_flowdc001",
                    cue=cue("seg_system001", 3, 4),
                    actions=(TargetAction(action="draw", targets=(EDC,)),),
                ),
                Beat(
                    beat_id="beat_flowac001",
                    cue=cue("seg_system001", 5, 6),
                    actions=(TargetAction(action="draw", targets=(EAC,)),),
                ),
                Beat(
                    beat_id="beat_flowrun01",
                    cue=cue("seg_system001", 7, 9),
                    actions=(TargetAction(action="flow", targets=(EDC, EAC)),),
                ),
                Beat(
                    beat_id="beat_flowzoom01",
                    cue=cue("seg_system001", 10, 12),
                    actions=(TargetAction(action="zoom_to", targets=(PANEL,)),),
                ),
            ),
        ),
    ),
)

content = box(96, 54, 1728, 972)
node_boxes = {
    SUN: box(336, 504, 224, 72),
    PANEL: box(848, 504, 256, 72),
    GRID: box(1392, 504, 192, 72),
}

timeline = ExplainerTimeline(
    timeline_id="tl_min0000001",
    spec_id=spec.spec_id,
    spec_hash=spec.spec_hash(),
    script_hash=spec.script_hash,
    pack_hash=spec.pack_hash,
    fps=FPS,
    width=1920,
    height=1080,
    total_frames=240,
    compiler_version="fixture-0.1",
    scenes=(
        CompiledExplainerScene(
            scene_id=BARS,
            start_frame=0,
            duration_frames=90,
            regions=(
                NamedRegion(name="title", box=box(96, 54, 1728, 56)),
                NamedRegion(name="legend", box=box(96, 118, 1728, 40)),
                NamedRegion(name="plot", box=box(96, 174, 1728, 852)),
            ),
            colors=(
                EntityColor(entity_id=WIND, token_id="data.cat.blue", srgb_hex=HEX["blue"]),
                EntityColor(entity_id=SOLAR, token_id="data.cat.orange", srgb_hex=HEX["orange"]),
            ),
            actions=(
                resolved("beat_barwind01", "reveal", 0, 12, WIND),
                resolved("beat_barsolar1", "reveal", 12, 24, SOLAR),
                resolved("beat_barhilit1", "highlight", 36, 42, SOLAR),
                resolved("beat_barannot1", "annotate", 48, 60, SOLAR),
                resolved("beat_barclear1", "clear_highlight", 78, 84),
            ),
        ),
        CompiledExplainerScene(
            scene_id=NUMBER,
            start_frame=90,
            duration_frames=60,
            regions=(NamedRegion(name="content", box=content),),
            boxes=(
                EntityBox(entity_id=NUM, box=box(96, 440, 1728, 90), font_px=72, lines=1),
                EntityBox(entity_id=NUMLBL, box=box(96, 546, 1728, 43), font_px=34, lines=1),
            ),
            actions=(
                resolved("beat_numshow01", "reveal", 90, 102, NUM),
                resolved("beat_numlabel1", "reveal", 100, 112, NUMLBL),
            ),
        ),
        CompiledExplainerScene(
            scene_id=FLOW,
            start_frame=150,
            duration_frames=90,
            regions=(NamedRegion(name="content", box=content),),
            colors=(
                EntityColor(entity_id=SUN, token_id="data.cat.orange", srgb_hex=HEX["orange"]),
                EntityColor(entity_id=PANEL, token_id="data.cat.blue", srgb_hex=HEX["blue"]),
                EntityColor(entity_id=GRID, token_id="data.cat.green", srgb_hex=HEX["green"]),
            ),
            boxes=tuple(
                EntityBox(entity_id=eid, box=b, font_px=28, lines=1)
                for eid, b in node_boxes.items()
            ),
            actions=(
                resolved("beat_flownodes1", "reveal", 150, 162, SUN, PANEL, GRID),
                resolved("beat_flowdc001", "draw", 165, 180, EDC),
                resolved("beat_flowac001", "draw", 180, 195, EAC),
                resolved("beat_flowrun01", "flow", 195, 207, EDC, EAC),
                resolved("beat_flowzoom01", "zoom_to", 213, 239, PANEL),
            ),
        ),
    ),
)

layout = DiagramLayout(
    scene_id=FLOW,
    width=1248,
    height=72,
    nodes=tuple(LayoutNode(entity_id=eid, box=b) for eid, b in node_boxes.items()),
    edges=(
        LayoutEdge(
            entity_id=EDC,
            points=(LayoutPoint(x=560, y=540), LayoutPoint(x=848, y=540)),
            label_anchor=LayoutPoint(x=704, y=512),
        ),
        LayoutEdge(
            entity_id=EAC,
            points=(LayoutPoint(x=1104, y=540), LayoutPoint(x=1392, y=540)),
            label_anchor=LayoutPoint(x=1248, y=512),
        ),
    ),
)

bundle = ExplainerRenderBundle(
    bundle_id="bndl_min00001",
    spec=spec,
    timeline=timeline,
    datasets=(dataset,),
    layouts=(layout,),
    design_system_version=1,
    fonts_version="fontsource-5.3.0",
)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(bundle.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n")
reloaded = ExplainerRenderBundle.model_validate_json(OUT.read_text())
assert reloaded.timeline.spec_hash == reloaded.spec.spec_hash()
print(
    json.dumps(
        {
            "out": str(OUT),
            "spec_hash": reloaded.spec.spec_hash(),
            "scenes": len(reloaded.timeline.scenes),
            "total_frames": reloaded.timeline.total_frames,
        }
    )
)
