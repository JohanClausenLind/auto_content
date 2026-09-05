import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { RenderBundle, SceneSpec } from "@content-factory/content-schema-ts";
import { editorialTheme } from "@content-factory/content-ui";
import { IMPLEMENTED_KINDS, PlaceholderCard, aspectFor, bulletRevealFrame, countUpValue, isImplementedKind, mapTimeline, msToFrames, sceneTitle, timelineIssues } from "../src/index";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..", "..", "..");
const bundle = JSON.parse(readFileSync(path.join(root, "fixtures/demo/timeline-bundle.json"), "utf8")) as RenderBundle;

const ALL_KINDS: SceneSpec["kind"][] = [
  "title", "section_intro", "big_number", "chart", "ranking", "comparison", "data_table", "timeline", "map", "flow_diagram",
  "relationship_diagram", "image", "screenshot", "quote", "definition", "bullet_sequence", "callout", "source_card", "chapter_transition",
  "manim_asset", "outro",
];

describe("timeline mapping", () => {
  it("maps every CompiledScene to a Sequence range with its SceneSpec", () => {
    const mapped = mapTimeline(bundle);
    expect(mapped).toHaveLength(bundle.timeline?.scenes.length ?? -1);
    expect(mapped.map((m) => [m.compiled.scene_id, m.from, m.durationInFrames])).toEqual([
      ["scn_title000001", 0, 126],
      ["scn_number00001", 126, 114],
      ["scn_bullets0001", 240, 180],
      ["scn_sources0001", 420, 98],
    ]);
    for (const m of mapped) {
      expect(m.spec?.scene_id).toBe(m.compiled.scene_id);
      expect(isImplementedKind(m.spec?.kind ?? "")).toBe(true);
    }
    expect(timelineIssues(bundle)).toEqual([]);
  });

  it("keeps scenes missing from the plan (placeholder) and reports them as issues", () => {
    const broken: RenderBundle = {
      ...bundle,
      timeline: { ...bundle.timeline!, scenes: [...bundle.timeline!.scenes, { beat_id: "beat_000000009", duration_frames: 30, scene_id: "scn_ghost000001", start_frame: 518, transition_in_frames: 0, word_cues: [] }], total_frames: 548 },
    };
    const mapped = mapTimeline(broken);
    expect(mapped[4]?.spec).toBeNull();
    expect(timelineIssues(broken)).toEqual(["scn_ghost000001 is not in the plan"]);
    expect(() => mapTimeline({ ...bundle, timeline: null })).toThrow(/no timeline/);
  });

  it("classifies every SceneSpec kind and titles them", () => {
    // Named, not counted: a count goes stale the moment a kind is implemented and says nothing
    // about which one moved. The complement below is the list that actually matters.
    expect(IMPLEMENTED_KINDS).toHaveLength(new Set(IMPLEMENTED_KINDS).size);
    expect(isImplementedKind("map")).toBe(true);
    expect(isImplementedKind("screenshot")).toBe(true);
    const unknown = ALL_KINDS.filter((k) => !isImplementedKind(k));
    expect([...unknown].sort()).toEqual(["data_table", "manim_asset", "ranking", "relationship_diagram"]);
    for (const scene of bundle.plan?.scenes ?? []) expect(sceneTitle(scene).length).toBeGreaterThan(0);
    const chart = { beat_id: "b", caption: null, chart: "line", data: { claim_id: null, column: null, dataset_id: "ds_x", row_key: null }, dual_axis: false, emphasis: [], encoding: {}, kind: "chart", scene_id: "scn_chart000001", source_ids: [], title: { claim_ids: [], text: "Wind share" }, truncation_disclosure: null, variant: "default", x: "year", y: ["share_pct"], zero_baseline: true } satisfies SceneSpec;
    expect(sceneTitle(chart)).toBe("Wind share");
  });

  it("renders a labelled deterministic placeholder for unknown kinds", () => {
    const html = renderToStaticMarkup(<PlaceholderCard kind="chart" title="Wind share" sceneId="scn_chart000001" width={1080} height={1920} theme={editorialTheme} />);
    expect(html).toContain('data-scene-placeholder="chart"');
    expect(html).toContain("placeholder · chart");
    expect(html).toContain("Wind share");
    expect(html).toContain("scn_chart000001");
    expect(html).toBe(renderToStaticMarkup(<PlaceholderCard kind="chart" title="Wind share" sceneId="scn_chart000001" width={1080} height={1920} theme={editorialTheme} />));
  });
});

describe("deterministic motion helpers", () => {
  it("converts ms to frames and picks aspects", () => {
    expect(msToFrames(360, 30)).toBe(11);
    expect(msToFrames(1, 30)).toBe(1);
    expect(aspectFor(1080, 1920)).toBe("9:16");
    expect(aspectFor(1920, 1080)).toBe("16:9");
    expect(aspectFor(1080, 1080)).toBe("1:1");
    expect(aspectFor(1080, 1350)).toBe("4:5");
  });

  it("reveals bullets on a fixed cadence inside the scene duration", () => {
    const frames = [0, 1, 2].map((i) => bulletRevealFrame(i, 3, 180, 11, 11));
    expect(frames).toEqual([11, 63, 115]);
    expect(frames[2]! + 11).toBeLessThanOrEqual(180);
  });

  it("counts up monotonically with the final value's precision", () => {
    expect(countUpValue(21, 0)).toBe(0);
    expect(countUpValue(21, 1)).toBe(21);
    expect(countUpValue(21, 0.5)).toBe(11);
    expect(countUpValue(3.75, 0.5)).toBe(1.88);
    let prev = -1;
    for (let t = 0; t <= 1; t += 0.01) {
      const v = countUpValue(1234, t);
      expect(v).toBeGreaterThanOrEqual(prev);
      prev = v;
    }
  });
});
