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

  it("names a source_document scene whose capture is not in the bundle", () => {
    const bundle = clone();
    const scene = bundle.spec.scenes[1];
    if (!scene) throw new Error("fixture has fewer than two scenes");
    bundle.spec.assets.push({ asset_id: "ast_capture001", kind: "capture", sha256: "a".repeat(64), dataset_id: null, capture_id: "cap_missing0001" });
    scene.template = { template: "source_document", capture_asset_id: "ast_capture001", initial_section_id: "sec_intro0001" };
    expect(bundleCapabilityErrors(bundle)).toEqual([`scene ${scene.scene_id} shows capture cap_missing0001, which is not in bundle.captures`]);
  });

  it("refuses a capture whose tiles are still local paths", () => {
    const bundle = clone();
    const scene = bundle.spec.scenes[1];
    if (!scene) throw new Error("fixture has fewer than two scenes");
    bundle.spec.assets.push({ asset_id: "ast_capture001", kind: "capture", sha256: "a".repeat(64), dataset_id: null, capture_id: "cap_local000001" });
    scene.template = { template: "source_document", capture_asset_id: "ast_capture001", initial_section_id: "sec_intro0001" };
    bundle.captures.push({
      capture_id: "cap_local000001",
      manifest: {
        schema_version: 1,
        capture_id: "cap_local000001",
        source_id: "src_fixture0001",
        url: "http://127.0.0.1/article.html",
        publisher: "",
        title: "",
        author: "",
        published_at: null,
        captured_at: "2026-09-22T00:00:00Z",
        capture_kind: "wacz",
        artifact_sha256: "b".repeat(64),
        signed: false,
        signature_domain: "",
        tls_certificate_sha256: null,
        viewport: { width: 1280, height: 800 },
        page_height_px: 1600,
        text_sha256: "c".repeat(64),
        extractor: "resolve-passages.mjs",
        extractor_version: "0.1.0",
        sections: [{ section_id: "sec_intro0001", heading: "", order: 0, scroll_y_px: 0 }],
        quotes: [],
        claim_ids: [],
      },
      tiles: [{ path: "/tmp/tiles/tile-0000.png", y_px: 0, width: 1280, height: 800, sha256: "d".repeat(64) }],
    });
    expect(bundleCapabilityErrors(bundle)).toEqual(["capture cap_local000001 tile /tmp/tiles/tile-0000.png is a local path; stage the bundle first (content_factory.explainer.render.stage_captures)"]);
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
