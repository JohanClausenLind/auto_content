#!/usr/bin/env node
// Render an artboard RenderBundle to a PNG still.
// usage: node scripts/render-artboard.mjs --bundle <path.json> --out <path.png> [--concurrency N]
// Prints one JSON line {"out","width","height","sha256"}; on error {"error"} to stderr, exit 1.
import { ensureBrowser, renderStill, selectComposition } from "@remotion/renderer";
import path from "node:path";

import { parseCli } from "./lib/args.mjs";
import { getServeUrl } from "./lib/bundle.mjs";
import { emit, fail, readBundle, sha256File, writeAtomically } from "./lib/output.mjs";
import { assertValid } from "./lib/validate.mjs";

try {
  const args = parseCli();
  const bundle = assertValid("RenderBundle", readBundle(args.bundle));
  if (bundle.kind !== "artboard" || !bundle.artboard) throw new Error(`bundle ${bundle.bundle_id} is not an artboard bundle`);
  const out = path.resolve(args.out);
  const inputProps = { bundle };

  await ensureBrowser();
  const serveUrl = await getServeUrl();
  const composition = await selectComposition({ serveUrl, id: "Artboard", inputProps, logLevel: "error" });
  // A still is a single frame: --concurrency is accepted for symmetry but has nothing to parallelize.
  await writeAtomically(out, (tmp) =>
    renderStill({ composition, serveUrl, output: tmp, inputProps, imageFormat: "png", scale: 1, logLevel: "error" }),
  );
  emit({ out, width: composition.width, height: composition.height, sha256: sha256File(out) });
} catch (err) {
  fail(err);
}
