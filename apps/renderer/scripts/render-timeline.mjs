#!/usr/bin/env node
// Render a timeline RenderBundle to an H.264 MP4 (yuv420p, bt709, muted).
// usage: node scripts/render-timeline.mjs --bundle <path.json> --out <path.mp4> [--scene <scene_id>] [--concurrency N]
// Prints one JSON line {"out","frames","fps","width","height","sha256"}; on error {"error"} to stderr, exit 1.
import { ensureBrowser, renderMedia, selectComposition } from "@remotion/renderer";
import path from "node:path";

import { parseCli } from "./lib/args.mjs";
import { getServeUrl } from "./lib/bundle.mjs";
import { emit, fail, readBundle, sha256File, writeAtomically } from "./lib/output.mjs";
import { assertValid } from "./lib/validate.mjs";

try {
  const args = parseCli({ scene: true });
  const bundle = assertValid("RenderBundle", readBundle(args.bundle));
  if (bundle.kind !== "timeline" || !bundle.timeline) throw new Error(`bundle ${bundle.bundle_id} is not a timeline bundle`);
  const out = path.resolve(args.out);
  const inputProps = { bundle };

  let frameRange = null;
  if (args.scene !== null) {
    const scene = bundle.timeline.scenes.find((s) => s.scene_id === args.scene);
    if (!scene) throw new Error(`scene ${args.scene} is not in the timeline`);
    frameRange = [scene.start_frame, scene.start_frame + scene.duration_frames - 1];
  }

  await ensureBrowser();
  const serveUrl = await getServeUrl();
  const composition = await selectComposition({ serveUrl, id: "Timeline", inputProps, logLevel: "error" });
  await writeAtomically(out, (tmp) =>
    renderMedia({
      composition,
      serveUrl,
      inputProps,
      outputLocation: tmp,
      codec: "h264",
      pixelFormat: "yuv420p",
      colorSpace: "bt709",
      muted: true,
      concurrency: args.concurrency,
      frameRange,
      logLevel: "error",
      onProgress: () => {},
    }),
  );
  const frames = frameRange ? frameRange[1] - frameRange[0] + 1 : composition.durationInFrames;
  emit({ out, frames, fps: composition.fps, width: composition.width, height: composition.height, sha256: sha256File(out) });
} catch (err) {
  fail(err);
}
