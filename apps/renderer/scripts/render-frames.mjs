#!/usr/bin/env node
// PNG frames of an ExplainerRenderBundle: --bundle --out-dir --frames 0,15,44 --mode sequential|stills.
// Both modes write f%06d.png, so the C1 gate can compare a played frame with a sought one pixel by pixel.
import { ensureBrowser, renderFrames, renderStill, selectComposition } from "@remotion/renderer";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

import { bundleCapabilityErrors } from "../../../packages/explainer-ui/src/capabilities.ts";
import { DEFAULT_CONCURRENCY, UsageError } from "./lib/args.mjs";
import { getServeUrl } from "./lib/bundle.mjs";
import { emit, fail, readBundle, sha256File } from "./lib/output.mjs";
import { assertValid } from "./lib/validate.mjs";

const USAGE = "usage: --bundle <path.json> --out-dir <dir> --frames <n,n,...> [--mode sequential|stills] [--concurrency N]";

function parseFramesCli() {
  const { values } = parseArgs({
    options: {
      bundle: { type: "string" },
      "out-dir": { type: "string" },
      frames: { type: "string" },
      mode: { type: "string" },
      concurrency: { type: "string" },
      help: { type: "boolean", short: "h" },
    },
    strict: true,
    allowPositionals: false,
  });
  if (values.help) throw new UsageError(USAGE);
  if (!values.bundle || !values["out-dir"] || !values.frames) throw new UsageError(USAGE);
  const frames = [...new Set(values.frames.split(",").map((s) => Number.parseInt(s.trim(), 10)))];
  if (frames.length === 0 || frames.some((f) => !Number.isInteger(f) || f < 0)) throw new UsageError("--frames takes non-negative integers separated by commas");
  const mode = values.mode ?? "stills";
  if (mode !== "sequential" && mode !== "stills") throw new UsageError("--mode is sequential or stills");
  const concurrency = values.concurrency === undefined ? DEFAULT_CONCURRENCY : Number.parseInt(values.concurrency, 10);
  if (!Number.isInteger(concurrency) || concurrency < 1) throw new UsageError("--concurrency must be a positive integer");
  return { bundle: values.bundle, outDir: path.resolve(values["out-dir"]), frames, mode, concurrency };
}

const fileFor = (outDir, frame) => path.join(outDir, `f${String(frame).padStart(6, "0")}.png`);

try {
  const args = parseFramesCli();
  const bundle = assertValid("ExplainerRenderBundle", readBundle(args.bundle));
  const unsupported = bundleCapabilityErrors(bundle);
  if (unsupported.length > 0) throw new Error(unsupported.join("\n"));
  const inputProps = { bundle };
  mkdirSync(args.outDir, { recursive: true });

  await ensureBrowser();
  const serveUrl = await getServeUrl();
  const composition = await selectComposition({ serveUrl, id: "Explainer", inputProps, logLevel: "error" });
  const last = Math.max(...args.frames);
  if (last >= composition.durationInFrames) throw new Error(`frame ${last} is past the last frame ${composition.durationInFrames - 1}`);

  if (args.mode === "sequential") {
    const wanted = new Set(args.frames);
    await renderFrames({
      composition,
      serveUrl,
      inputProps,
      outputDir: null,
      imageFormat: "png",
      frameRange: [Math.min(...args.frames), last],
      concurrency: args.concurrency,
      logLevel: "error",
      onStart: () => {},
      onFrameUpdate: () => {},
      onFrameBuffer: (buffer, frame) => {
        if (wanted.has(frame)) writeFileSync(fileFor(args.outDir, frame), buffer);
      },
    });
  } else {
    for (const frame of args.frames) {
      await renderStill({ composition, serveUrl, inputProps, output: fileFor(args.outDir, frame), frame, imageFormat: "png", licenseKey: "free-license", logLevel: "error" });
    }
  }
  const files = args.frames.map((frame) => ({ frame, file: fileFor(args.outDir, frame), sha256: sha256File(fileFor(args.outDir, frame)) }));
  emit({ outDir: args.outDir, mode: args.mode, fps: composition.fps, width: composition.width, height: composition.height, frames: files });
} catch (err) {
  fail(err);
}
