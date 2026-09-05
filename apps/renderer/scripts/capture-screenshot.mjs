#!/usr/bin/env node
// Capture a web page to a PNG for ScreenshotScene, with every knob set for repeatability.
// usage: node scripts/capture-screenshot.mjs --url <url> --out <path.png>
//        [--width 1440] [--height 900] [--scale 2] [--full-page] [--settle-ms 0]
// Prints one JSON line {"out","url","width","height","deviceScaleFactor","sha256"}.
//
// This is a CAPTURE step, run by an operator before a render — never from a workflow activity and
// never from `just test`, which is required to run with no internet. The renderer only ever sees
// the resulting file as a bundle asset. Tests that need a screenshot use a committed fixture PNG.
import { parseArgs } from "node:util";

import { chromium } from "playwright";

import { emit, fail, sha256File, writeAtomically } from "./lib/output.mjs";

try {
  const { values } = parseArgs({
    options: {
      url: { type: "string" },
      out: { type: "string" },
      width: { type: "string" },
      height: { type: "string" },
      scale: { type: "string" },
      "full-page": { type: "boolean" },
      "settle-ms": { type: "string" },
      help: { type: "boolean", short: "h" },
    },
    strict: true,
    allowPositionals: false,
  });
  if (values.help) throw new Error("usage: --url <url> --out <path.png> [--width N] [--height N] [--scale N] [--full-page] [--settle-ms N]");
  if (!values.url) throw new Error("--url is required");
  if (!values.out) throw new Error("--out <path.png> is required");
  if (!/^https?:\/\//.test(values.url)) throw new Error("--url must be http(s)");

  const width = Number.parseInt(values.width ?? "1440", 10);
  const height = Number.parseInt(values.height ?? "900", 10);
  const deviceScaleFactor = Number.parseInt(values.scale ?? "2", 10);
  const settleMs = Number.parseInt(values["settle-ms"] ?? "0", 10);
  for (const [name, n] of [["width", width], ["height", height], ["scale", deviceScaleFactor]]) {
    if (!Number.isInteger(n) || n < 1) throw new Error(`--${name} must be a positive integer`);
  }

  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({
      viewport: { width, height },
      deviceScaleFactor,
      // Repeatability: freeze CSS animation, pin the theme and locale rather than inheriting the
      // host's, so the same URL captured on two machines differs only by the page's own content.
      reducedMotion: "reduce",
      colorScheme: "light",
      locale: "en-GB",
      timezoneId: "UTC",
    });
    await page.goto(values.url, { waitUntil: "networkidle", timeout: 60_000 });
    // Belt and braces over reducedMotion: kill any JS-driven animation still running.
    await page.addStyleTag({
      content: "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}",
    });
    if (settleMs > 0) await page.waitForTimeout(settleMs);
    await writeAtomically(values.out, (tmp) => page.screenshot({ path: tmp, fullPage: Boolean(values["full-page"]) }));
  } finally {
    await browser.close();
  }
  emit({ out: values.out, url: values.url, width, height, deviceScaleFactor, sha256: sha256File(values.out) });
} catch (err) {
  fail(err);
}
