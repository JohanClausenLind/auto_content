// Webpack bundle cache: one bundle per hash of (renderer src, content-ui src, video-ui src,
// fixtures imported as default props, fonts, lockfile). Reused across script invocations.
import { bundle } from "@remotion/bundler";
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
export const rendererRoot = path.resolve(here, "..", "..");
export const repoRoot = path.resolve(rendererRoot, "..", "..");
export const publicDir = path.join(rendererRoot, "public");

const FONT_FILES = [400, 500, 600, 700].map((w) => `inter-latin-${w}-normal.woff2`);

/** Copy the pinned Inter faces from node_modules into public/fonts (Remotion serves public/). */
export function ensureFonts() {
  const fontDir = path.dirname(require.resolve("@fontsource/inter/package.json"));
  mkdirSync(path.join(publicDir, "fonts"), { recursive: true });
  for (const file of FONT_FILES) {
    const dst = path.join(publicDir, "fonts", file);
    if (!existsSync(dst)) copyFileSync(path.join(fontDir, "files", file), dst);
  }
}

function walk(dir, out) {
  if (!existsSync(dir)) return;
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules") continue;
      walk(p, out);
    } else if (entry.isFile()) out.push(p);
  }
}

export function bundleHash() {
  const inputs = [];
  for (const dir of [
    path.join(rendererRoot, "src"),
    path.join(rendererRoot, "public"),
    path.join(repoRoot, "packages", "content-ui", "src"),
    path.join(repoRoot, "packages", "video-ui", "src"),
    path.join(repoRoot, "fixtures", "demo"),
  ])
    walk(dir, inputs);
  inputs.push(path.join(repoRoot, "pnpm-lock.yaml"));
  const h = createHash("sha256");
  for (const file of inputs) {
    if (!existsSync(file) || !statSync(file).isFile()) continue;
    h.update(path.relative(repoRoot, file));
    h.update("\0");
    h.update(readFileSync(file));
    h.update("\0");
  }
  return h.digest("hex").slice(0, 24);
}

/** Returns the serve URL (bundle directory), building it only when the hash is new. */
export async function getServeUrl() {
  ensureFonts();
  const cacheDir = path.join(rendererRoot, ".remotion-bundle");
  const key = bundleHash();
  const target = path.join(cacheDir, key);
  if (existsSync(path.join(target, "index.html"))) return target;
  mkdirSync(cacheDir, { recursive: true });
  for (const entry of readdirSync(cacheDir)) {
    if (entry !== key) rmSync(path.join(cacheDir, entry), { recursive: true, force: true });
  }
  const tmp = path.join(cacheDir, `tmp-${process.pid}`);
  rmSync(tmp, { recursive: true, force: true });
  await bundle({
    entryPoint: path.join(rendererRoot, "src", "index.ts"),
    publicDir,
    outDir: tmp,
    onProgress: () => {},
  });
  renameSync(tmp, target);
  return target;
}
