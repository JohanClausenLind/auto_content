import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { smokeTitleSchema } from "../src/compositions/SmokeTitle";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(here, "..");

describe("renderer", () => {
  it("SmokeTitle props schema accepts the default props and rejects unknown shapes", () => {
    expect(smokeTitleSchema.safeParse({ title: "a", subtitle: "b", seed: "c" }).success).toBe(true);
    expect(smokeTitleSchema.safeParse({ title: 1 }).success).toBe(false);
  });

  it("pins the local font through the lockfile (SIL OFL 1.1)", () => {
    const font = path.join(root, "node_modules", "@fontsource", "inter", "files", "inter-latin-700-normal.woff2");
    expect(existsSync(font)).toBe(true);
    const pkg = JSON.parse(readFileSync(path.join(root, "node_modules", "@fontsource", "inter", "package.json"), "utf8"));
    expect(String(pkg.license)).toMatch(/OFL/);
  });

  it("smoke render output, when present, is fast-start MP4 (moov before mdat)", () => {
    const out = path.join(root, "out", "smoke-title.mp4");
    if (!existsSync(out)) return; // rendered by `just render-smoke`; not required for core CI
    const buf = readFileSync(out);
    expect(buf.indexOf("moov")).toBeGreaterThan(0);
    expect(buf.indexOf("moov")).toBeLessThan(buf.indexOf("mdat"));
  });
});
