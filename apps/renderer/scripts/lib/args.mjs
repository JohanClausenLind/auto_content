import { parseArgs } from "node:util";

export const DEFAULT_CONCURRENCY = 4;

/** Parse `--bundle <path> --out <path> [--concurrency N] [--scene id] [--scale N] [--crf N]` with node:util only. */
export function parseCli(extra = {}) {
  const options = {
    bundle: { type: "string" },
    out: { type: "string" },
    concurrency: { type: "string" },
    scale: { type: "string" },
    help: { type: "boolean", short: "h" },
  };
  if (extra.scene) options.scene = { type: "string" };
  if (extra.crf) options.crf = { type: "string" };
  const { values } = parseArgs({ options, strict: true, allowPositionals: false });
  if (values.help) {
    const scene = extra.scene ? " [--scene <scene_id>]" : "";
    const crf = extra.crf ? " [--crf N]" : "";
    throw new UsageError(`usage: --bundle <path.json> --out <path>${scene} [--concurrency N] [--scale N]${crf}`);
  }
  const crf = values.crf === undefined ? null : Number.parseInt(values.crf, 10);
  if (crf !== null && (!Number.isInteger(crf) || crf < 1 || crf > 51)) throw new UsageError("--crf must be an integer between 1 and 51");
  if (!values.bundle) throw new UsageError("--bundle <path.json> is required");
  if (!values.out) throw new UsageError("--out <path> is required");
  const concurrency = values.concurrency === undefined ? DEFAULT_CONCURRENCY : Number.parseInt(values.concurrency, 10);
  if (!Number.isInteger(concurrency) || concurrency < 1) throw new UsageError("--concurrency must be a positive integer");
  // A retina export: the composition keeps its declared size and every pixel is rendered N times
  // over, so the PNG comes back N times larger in each dimension.
  const scale = values.scale === undefined ? 1 : Number.parseInt(values.scale, 10);
  if (!Number.isInteger(scale) || scale < 1 || scale > 4) throw new UsageError("--scale must be an integer between 1 and 4");
  return { bundle: values.bundle, out: values.out, concurrency, scale, scene: values.scene ?? null, crf };
}

export class UsageError extends Error {}
