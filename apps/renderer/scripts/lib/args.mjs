import { parseArgs } from "node:util";

export const DEFAULT_CONCURRENCY = 4;

/**
 * Parse `--bundle <path> --out <path> [--concurrency N] [--scene id]` with node:util only.
 * @param {{scene?: boolean}} extra
 */
export function parseCli(extra = {}) {
  const options = {
    bundle: { type: "string" },
    out: { type: "string" },
    concurrency: { type: "string" },
    help: { type: "boolean", short: "h" },
  };
  if (extra.scene) options.scene = { type: "string" };
  const { values } = parseArgs({ options, strict: true, allowPositionals: false });
  if (values.help) {
    const scene = extra.scene ? " [--scene <scene_id>]" : "";
    throw new UsageError(`usage: --bundle <path.json> --out <path>${scene} [--concurrency N]`);
  }
  if (!values.bundle) throw new UsageError("--bundle <path.json> is required");
  if (!values.out) throw new UsageError("--out <path> is required");
  const concurrency = values.concurrency === undefined ? DEFAULT_CONCURRENCY : Number.parseInt(values.concurrency, 10);
  if (!Number.isInteger(concurrency) || concurrency < 1) throw new UsageError("--concurrency must be a positive integer");
  return { bundle: values.bundle, out: values.out, concurrency, scene: values.scene ?? null };
}

export class UsageError extends Error {}
