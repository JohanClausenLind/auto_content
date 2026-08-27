// Phase-0 spike: bundle, render a 3s 1080p clip with a local font, then ffprobe-assert the output.
import { bundle } from "@remotion/bundler";
import { ensureBrowser, renderMedia, selectComposition } from "@remotion/renderer";
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const publicDir = path.join(root, "public");
const outDir = path.join(root, "out");
mkdirSync(path.join(publicDir, "fonts"), { recursive: true });
mkdirSync(outDir, { recursive: true });

// Materialize the pinned font file from node_modules into public/ (Remotion serves public/).
const fontSrc = path.join(root, "node_modules", "@fontsource", "inter", "files", "inter-latin-700-normal.woff2");
const fontDst = path.join(publicDir, "fonts", "inter-latin-700-normal.woff2");
if (!existsSync(fontDst)) copyFileSync(fontSrc, fontDst);

await ensureBrowser(); // downloads Chrome Headless Shell once into node_modules/.remotion
const serveUrl = await bundle({ entryPoint: path.join(root, "src", "index.ts"), publicDir });
const inputProps = { title: "Content Factory", subtitle: "offline smoke render", seed: "smoke" };
const composition = await selectComposition({ serveUrl, id: "SmokeTitle", inputProps });
const outputLocation = path.join(outDir, "smoke-title.mp4");
await renderMedia({
  composition,
  serveUrl,
  codec: "h264",
  outputLocation,
  inputProps,
  pixelFormat: "yuv420p",
  colorSpace: "bt709",
  muted: true,
  onProgress: () => {},
});

const probe = spawnSync(
  "ffprobe",
  ["-v", "error", "-print_format", "json", "-show_streams", "-show_format", outputLocation],
  { encoding: "utf8" },
);
if (probe.status !== 0) throw new Error(`ffprobe failed: ${probe.stderr}`);
const info = JSON.parse(probe.stdout);
const video = info.streams.find((s) => s.codec_type === "video");
const assertions = {
  codec: video.codec_name === "h264",
  size: video.width === 1920 && video.height === 1080,
  fps: video.r_frame_rate === "30/1",
  pix_fmt: video.pix_fmt === "yuv420p",
  frames: Number(video.nb_frames) === 90,
  faststart: moovBeforeMdat(outputLocation),
};
console.log(JSON.stringify({ outputLocation, assertions, duration: info.format.duration }, null, 2));
if (!Object.values(assertions).every(Boolean)) {
  process.exit(1);
}

/** MP4 "fast start": the moov atom must precede mdat so playback can begin before full download. */
function moovBeforeMdat(file) {
  const buf = readFileSync(file);
  const moov = buf.indexOf("moov");
  const mdat = buf.indexOf("mdat");
  return moov >= 0 && mdat >= 0 && moov < mdat;
}
