import type { ExplainerRenderBundle } from "@content-factory/content-schema-ts";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { bundleCapabilityErrors, calculateExplainerMetadata } from "../src/capabilities";

const fixture = JSON.parse(readFileSync(new URL("../../../fixtures/explainer/render/bundle-min.json", import.meta.url), "utf8")) as ExplainerRenderBundle;

function clone(): ExplainerRenderBundle {
  return JSON.parse(JSON.stringify(fixture)) as ExplainerRenderBundle;
}

describe("bundleCapabilityErrors", () => {
  it("accepts the minimal fixture", () => {
    expect(bundleCapabilityErrors(fixture)).toEqual([]);
  });

  it("names every source_document scene as a Phase 3 capability", () => {
    const bundle = clone();
    const scene = bundle.spec.scenes[1];
    if (!scene) throw new Error("fixture has fewer than two scenes");
    scene.template = { template: "source_document", capture_asset_id: "ast_capture001", initial_section_id: "sec_intro0001" };
    const errors = bundleCapabilityErrors(bundle);
    expect(errors).toEqual([`scene ${scene.scene_id} uses source_document; the source_document renderer arrives in Phase 3`]);
  });

  it("names a diagram scene whose layout is missing", () => {
    const bundle = clone();
    bundle.layouts = [];
    const diagram = bundle.spec.scenes.find((s) => s.template.template === "diagram");
    expect(diagram).toBeDefined();
    expect(bundleCapabilityErrors(bundle)).toEqual([`scene ${diagram?.scene_id} is a diagram without a DiagramLayout in bundle.layouts`]);
  });

  it("reads composition metadata from the timeline", async () => {
    const meta = await calculateExplainerMetadata({ props: { bundle: fixture }, defaultProps: { bundle: fixture }, abortSignal: new AbortController().signal, compositionId: "Explainer", isRendering: true });
    expect(meta).toMatchObject({ fps: fixture.timeline.fps, width: fixture.timeline.width, height: fixture.timeline.height, durationInFrames: fixture.timeline.total_frames });
  });
});
