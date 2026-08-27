import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, rmSync } from "node:fs";
import path from "node:path";

export function sha256File(file) {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

/** Produce into a sibling temp file (same extension, so encoders infer the format), then rename. */
export async function writeAtomically(finalPath, produce) {
  mkdirSync(path.dirname(finalPath), { recursive: true });
  const ext = path.extname(finalPath);
  const tmp = path.join(path.dirname(finalPath), `.${path.basename(finalPath, ext)}.tmp-${process.pid}${ext}`);
  rmSync(tmp, { force: true });
  try {
    await produce(tmp);
    renameSync(tmp, finalPath);
  } catch (err) {
    rmSync(tmp, { force: true });
    throw err;
  }
}

export function emit(obj) {
  process.stdout.write(`${JSON.stringify(obj)}\n`);
}

export function fail(err) {
  const message = err instanceof Error ? err.message : String(err);
  process.stderr.write(`${JSON.stringify({ error: message })}\n`);
  process.exit(1);
}

export function readBundle(file) {
  return JSON.parse(readFileSync(file, "utf8"));
}
