// Determinism: identical inputs must produce identical bytes (PNG) and identical decoded frames (MP4).
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");
const repo = path.join(root, "..", "..");
const outDir = path.join(root, "out", "test");
const SCENE = "scn_number00001";
const SCENE_FRAMES = 114;

interface ScriptResult {
  status: number | null;
  json: Record<string, unknown>;
  stderr: string;
}

function runScript(script: string, args: string[]): ScriptResult {
  const r = spawnSync(process.execPath, [path.join(root, "scripts", script), ...args], { cwd: root, encoding: "utf8", timeout: 280_000 });
  const lines = r.stdout.trim().split("\n").filter(Boolean);
  const last = lines[lines.length - 1] ?? "{}";
  let json: Record<string, unknown> = {};
  try {
    json = JSON.parse(last) as Record<string, unknown>;
  } catch {
    json = { unparsed: last };
  }
  return { status: r.status, json, stderr: r.stderr };
}

const sha256 = (file: string) => createHash("sha256").update(readFileSync(file)).digest("hex");

function pngSize(file: string): { width: number; height: number } {
  const buf = readFileSync(file);
  expect(buf.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

function ffprobe(file: string): { streams: Array<Record<string, unknown>>; format: Record<string, unknown> } {
  const r = spawnSync("ffprobe", ["-v", "error", "-print_format", "json", "-show_streams", "-show_format", "-count_frames", file], { encoding: "utf8" });
  expect(r.status, r.stderr).toBe(0);
  return JSON.parse(r.stdout) as { streams: Array<Record<string, unknown>>; format: Record<string, unknown> };
}

function extractFrames(file: string, dir: string, frames: number[]): string[] {
  mkdirSync(dir, { recursive: true });
  const select = frames.map((n) => `eq(n\\,${n})`).join("+");
  const r = spawnSync("ffmpeg", ["-v", "error", "-y", "-i", file, "-vf", `select='${select}'`, "-fps_mode", "vfr", "-f", "image2", path.join(dir, "frame-%d.png")], { encoding: "utf8" });
  expect(r.status, r.stderr).toBe(0);
  return frames.map((_, i) => path.join(dir, `frame-${i + 1}.png`));
}

describe("render scripts are deterministic", () => {
  rmSync(outDir, { recursive: true, force: true });
  mkdirSync(outDir, { recursive: true });

  it("render-artboard: two runs of the fixture produce byte-identical PNGs", () => {
    const bundle = path.join(repo, "fixtures", "demo", "artboard-bundle.json");
    const a = path.join(outDir, "artboard-a.png");
    const b = path.join(outDir, "artboard-b.png");
    const ra = runScript("render-artboard.mjs", ["--bundle", bundle, "--out", a]);
    expect(ra.status, ra.stderr).toBe(0);
    const rb = runScript("render-artboard.mjs", ["--bundle", bundle, "--out", b]);
    expect(rb.status, rb.stderr).toBe(0);
    expect(ra.json).toMatchObject({ out: a, width: 1080, height: 1080 });
    expect(rb.json).toMatchObject({ out: b, width: 1080, height: 1080 });
    expect(pngSize(a)).toEqual({ width: 1080, height: 1080 });
    expect(sha256(a)).toBe(ra.json.sha256);
    expect(sha256(b)).toBe(rb.json.sha256);
    expect(sha256(a)).toBe(sha256(b));
  });

  it("render-artboard: invalid bundle -> {error} on stderr, exit 1", () => {
    const bad = path.join(outDir, "bad-bundle.json");
    const fixture = JSON.parse(readFileSync(path.join(repo, "fixtures", "demo", "artboard-bundle.json"), "utf8")) as Record<string, unknown>;
    (fixture as { schema_version: unknown }).schema_version = "one";
    (fixture as { unexpected?: unknown }).unexpected = true;
    writeFileSync(bad, JSON.stringify(fixture));
    const r = runScript("render-artboard.mjs", ["--bundle", bad, "--out", path.join(outDir, "never.png")]);
    expect(r.status).toBe(1);
    const err = JSON.parse(r.stderr.trim().split("\n").pop() ?? "{}") as { error?: string };
    expect(err.error).toMatch(/RenderBundle failed validation/);
    expect(existsSync(path.join(outDir, "never.png"))).toBe(false);
  });

  it(`render-timeline --scene ${SCENE}: two runs decode to identical frames and probe as h264/yuv420p 1080x1920@30 with ${SCENE_FRAMES} frames`, () => {
    const bundle = path.join(repo, "fixtures", "demo", "timeline-bundle.json");
    const a = path.join(outDir, "scene-a.mp4");
    const b = path.join(outDir, "scene-b.mp4");
    const ra = runScript("render-timeline.mjs", ["--bundle", bundle, "--out", a, "--scene", SCENE]);
    expect(ra.status, ra.stderr).toBe(0);
    const rb = runScript("render-timeline.mjs", ["--bundle", bundle, "--out", b, "--scene", SCENE]);
    expect(rb.status, rb.stderr).toBe(0);
    expect(ra.json).toMatchObject({ out: a, frames: SCENE_FRAMES, fps: 30, width: 1080, height: 1920 });
    expect(sha256(a)).toBe(ra.json.sha256);

    // Container/codec assertions.
    for (const file of [a, b]) {
      const info = ffprobe(file);
      const video = info.streams.find((s) => s.codec_type === "video");
      expect(video).toBeDefined();
      expect(video).toMatchObject({ codec_name: "h264", pix_fmt: "yuv420p", width: 1080, height: 1920, r_frame_rate: "30/1" });
      expect(Number(video?.nb_read_frames ?? video?.nb_frames)).toBe(SCENE_FRAMES);
      expect(info.streams.filter((s) => s.codec_type === "audio")).toHaveLength(0);
      const buf = readFileSync(file);
      const moov = buf.indexOf("moov");
      const mdat = buf.indexOf("mdat");
      expect(moov).toBeGreaterThan(0);
      expect(mdat).toBeGreaterThan(0);
      expect(moov).toBeLessThan(mdat);
    }

    // Frame-level equality is the acceptance criterion; byte equality of the MP4 is reported.
    const mp4Identical = sha256(a) === sha256(b);
    const frames = [0, Math.floor(SCENE_FRAMES / 2), SCENE_FRAMES - 1];
    const fa = extractFrames(a, path.join(outDir, "frames-a"), frames);
    const fb = extractFrames(b, path.join(outDir, "frames-b"), frames);
    for (let i = 0; i < frames.length; i++) {
      expect(existsSync(fa[i]!), `frame ${frames[i]} of run A`).toBe(true);
      expect(existsSync(fb[i]!), `frame ${frames[i]} of run B`).toBe(true);
      expect(pngSize(fa[i]!)).toEqual({ width: 1080, height: 1920 });
      expect(sha256(fa[i]!), `frame ${frames[i]} differs between runs`).toBe(sha256(fb[i]!));
    }
    // eslint-disable-next-line no-console
    console.log(JSON.stringify({ mp4Identical, framesCompared: frames, frameIdentical: true }));
  });
});
