import { validate, type ExplainerRenderBundle } from "@content-factory/content-schema-ts";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const fixture = JSON.parse(readFileSync(new URL("../../../fixtures/explainer/render/bundle-min.json", import.meta.url), "utf8")) as ExplainerRenderBundle;

describe("fixtures/explainer/render/bundle-min.json", () => {
  it("validates against the ExplainerRenderBundle schema", () => {
    const result = validate("ExplainerRenderBundle", fixture);
    expect(result.errors).toEqual([]);
    expect(result.ok).toBe(true);
  });

  it("is contiguous from frame 0 and sums to total_frames", () => {
    let cursor = 0;
    for (const scene of fixture.timeline.scenes) {
      expect(scene.start_frame).toBe(cursor);
      cursor += scene.duration_frames;
    }
    expect(cursor).toBe(fixture.timeline.total_frames);
  });

  it("covers the three Phase 2 templates once each", () => {
    expect(fixture.spec.scenes.map((s) => s.template.template).sort()).toEqual(["chart", "diagram", "text"]);
  });
});
