import type { CompiledExplainerScene, DiagramTemplate as DiagramSpec, ExplainerRenderBundle, Scene, TextTemplate as TextSpec } from "@content-factory/content-schema-ts";
import { readFileSync } from "node:fs";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SceneEnvContext, buildSceneEnv } from "../src/context";
import { regionOf } from "../src/geometry";
import { resolveSceneState } from "../src/state";
import { DiagramTemplate } from "../src/templates/DiagramTemplate";
import { TextTemplate } from "../src/templates/TextTemplate";
import { lineHeightPx } from "../src/text";
import { TOKENS } from "../src/tokens.gen";

const bundle = JSON.parse(readFileSync(new URL("../../../fixtures/explainer/render/bundle-min.json", import.meta.url), "utf8")) as ExplainerRenderBundle;
const env = buildSceneEnv(bundle);

function sceneNamed(id: string): { compiled: CompiledExplainerScene; scene: Scene } {
  const compiled = bundle.timeline.scenes.find((s) => s.scene_id === id);
  const scene = env.scenes.get(id);
  if (!compiled || !scene) throw new Error(`bundle-min has no scene ${id}`);
  return { compiled, scene };
}

function markup(element: ReactElement): string {
  return renderToStaticMarkup(createElement(SceneEnvContext.Provider, { value: env }, element));
}

/** React serialises a zero length without its unit. */
function px(v: number): string {
  return v === 0 ? "0" : `${v}px`;
}

function rect(x: number, y: number, width: number, height: number): string {
  return `position:absolute;left:${px(x)};top:${px(y)};width:${px(width)};height:${px(height)}`;
}

describe("DiagramTemplate", () => {
  it("draws every node rect at the DiagramLayout node box", () => {
    const { compiled, scene } = sceneNamed("scn_flow0001");
    const layout = env.layouts.get(scene.scene_id);
    if (!layout || scene.template.template !== "diagram") throw new Error("scn_flow0001 is not a laid-out diagram");
    const canvas = { width: bundle.timeline.width, height: bundle.timeline.height };
    const state = resolveSceneState(compiled, scene, compiled.duration_frames - 1, bundle.timeline.fps, { layout, canvas });
    const html = markup(<DiagramTemplate compiled={compiled} scene={scene} template={scene.template as DiagramSpec} state={state} layout={layout} />);
    for (const node of layout.nodes) {
      const b = node.box;
      expect(html).toContain(`<rect x="${b.x}" y="${b.y}" width="${b.width}" height="${b.height}"`);
      expect(html).toContain(`<svg x="${b.x}" y="${b.y}" width="${b.width}" height="${b.height}"`);
    }
  });
});

describe("TextTemplate", () => {
  it("renders each item at its compiled box in canvas pixels", () => {
    const { compiled, scene } = sceneNamed("scn_num00001");
    if (scene.template.template !== "text") throw new Error("scn_num00001 is not a text scene");
    const content = regionOf(compiled, "content");
    if (!content) throw new Error("scn_num00001 has no content region");
    const state = resolveSceneState(compiled, scene, compiled.duration_frames - 1, bundle.timeline.fps);
    const html = markup(<TextTemplate compiled={compiled} scene={scene} template={scene.template as TextSpec} state={state} />);
    expect(html).toContain(rect(content.x, content.y, content.width, content.height));
    expect(compiled.boxes.length).toBeGreaterThan(0);
    const pad = TOKENS.layout.grid.unit / 2;
    for (const box of compiled.boxes) {
      const b = box.box;
      if (box.font_px === null) throw new Error(`${box.entity_id} has no font_px`);
      const block = Math.min(b.height, box.lines * lineHeightPx(box.font_px));
      expect(html).toContain(rect(b.x + pad - content.x, b.y + (b.height - block) / 2 - content.y, b.width - 2 * pad, block));
    }
  });
});
