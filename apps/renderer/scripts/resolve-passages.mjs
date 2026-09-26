#!/usr/bin/env node
// Locate quoted passages in a (replayed) page: DOM ranges, line rects in page pixels, the
// enclosing heading, and viewport-height PNG tiles. Prints one JSON document; exit 2 = quote errors.
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";

import { chromium } from "playwright";

const CONTEXT_CHARS = 120;
const FREEZE_CSS =
  "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}" +
  "html{scroll-behavior:auto!important}";

/** Runs inside the page: force lazy images, pin sticky/fixed elements, then wait for every image. */
async function settlePage() {
  for (const img of document.querySelectorAll("img[loading='lazy']")) img.loading = "eager";
  const height = () => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight);
  const step = window.innerHeight;
  // One controlled pass down the page so IntersectionObserver-driven loaders fire before we measure.
  for (let y = 0; y < height(); y += step) {
    window.scrollTo(0, y);
    await new Promise((r) => setTimeout(r, 40));
  }
  window.scrollTo(0, 0);
  for (const el of document.querySelectorAll("*")) {
    const position = getComputedStyle(el).position;
    if (position === "sticky" || position === "fixed") el.style.setProperty("position", "static", "important");
  }
  const pending = [...document.images]
    .filter((img) => !img.complete)
    .map(
      (img) =>
        new Promise((resolve) => {
          img.addEventListener("load", resolve, { once: true });
          img.addEventListener("error", resolve, { once: true });
        }),
    );
  await Promise.race([Promise.all(pending), new Promise((r) => setTimeout(r, 10_000))]);
  window.scrollTo(0, 0);
  return height();
}

/** Runs inside the page: find each quote's occurrences and measure the requested one. */
function locateQuotes({ requests, contextChars }) {
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"]);
  const nodes = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const parent = node.parentElement;
      if (!parent || SKIP.has(parent.tagName)) return NodeFilter.FILTER_REJECT;
      if (!parent.checkVisibility({ checkVisibilityCSS: true })) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node);

  // Whitespace runs collapse to one space; every kept character remembers its text node and offset.
  const chars = [];
  const at = [];
  let lastSpace = true;
  nodes.forEach((node, nodeIndex) => {
    const data = node.data;
    for (let offset = 0; offset < data.length; offset += 1) {
      const ch = data[offset];
      if (/\s/.test(ch)) {
        if (lastSpace) continue;
        chars.push(" ");
        at.push([nodeIndex, offset]);
        lastSpace = true;
      } else {
        chars.push(ch);
        at.push([nodeIndex, offset]);
        lastSpace = false;
      }
    }
  });
  const haystack = chars.join("");
  const normalize = (text) => text.replace(/\s+/g, " ").trim();

  const nodePath = (node) => {
    const parts = [];
    for (let cur = node; cur && cur !== document.documentElement; cur = cur.parentNode) {
      const parent = cur.parentNode;
      if (cur.nodeType === Node.TEXT_NODE) {
        const texts = [...parent.childNodes].filter((n) => n.nodeType === Node.TEXT_NODE);
        parts.unshift(`#text[${texts.indexOf(cur)}]`);
      } else {
        const same = [...parent.children].filter((e) => e.tagName === cur.tagName);
        parts.unshift(`${cur.tagName.toLowerCase()}[${same.indexOf(cur)}]`);
      }
    }
    return `html/${parts.join("/")}`;
  };

  const headings = [...document.querySelectorAll("h1, h2, h3")].filter((h) =>
    h.checkVisibility({ checkVisibilityCSS: true }),
  );
  const sections = headings.map((h) => ({
    heading: normalize(h.innerText),
    y_px: h.getBoundingClientRect().top + window.scrollY,
  }));
  const sectionIndexFor = (node) => {
    let found = null;
    headings.forEach((h, index) => {
      const relation = h.compareDocumentPosition(node);
      if (relation & Node.DOCUMENT_POSITION_FOLLOWING || relation & Node.DOCUMENT_POSITION_CONTAINED_BY) found = index;
    });
    return found;
  };

  const mergeLines = (rects) => {
    const sorted = rects
      .filter((r) => r.width > 0 && r.height > 0)
      .map((r) => ({ x: r.left + window.scrollX, y: r.top + window.scrollY, width: r.width, height: r.height }))
      .sort((a, b) => a.y - b.y || a.x - b.x);
    const lines = [];
    for (const rect of sorted) {
      const last = lines.at(-1);
      const overlap = last ? Math.min(last.y + last.height, rect.y + rect.height) - Math.max(last.y, rect.y) : 0;
      if (last && overlap >= 0.5 * Math.min(last.height, rect.height)) {
        const x = Math.min(last.x, rect.x);
        const y = Math.min(last.y, rect.y);
        last.width = Math.max(last.x + last.width, rect.x + rect.width) - x;
        last.height = Math.max(last.y + last.height, rect.y + rect.height) - y;
        last.x = x;
        last.y = y;
      } else {
        lines.push({ ...rect });
      }
    }
    return lines;
  };

  const results = [];
  const errors = [];
  requests.forEach((request, index) => {
    const needle = normalize(request.text);
    const starts = [];
    for (let from = haystack.indexOf(needle); from !== -1 && needle; from = haystack.indexOf(needle, from + needle.length)) {
      starts.push(from);
    }
    const count = starts.length;
    const wanted = request.occurrence_index ?? null;
    if (count === 0 || (wanted !== null && wanted >= count)) {
      results.push({ index, status: "passage_not_found", occurrence_count: count });
      errors.push({ code: "passage_not_found", quote_index: index, count });
      return;
    }
    if (count > 1 && wanted === null) {
      results.push({ index, status: "passage_ambiguous", occurrence_count: count });
      errors.push({ code: "passage_ambiguous", quote_index: index, count });
      return;
    }
    const start = starts[wanted ?? 0];
    const end = start + needle.length - 1;
    const [startNode, startOffset] = at[start];
    const [endNode, endOffset] = at[end];
    const range = document.createRange();
    range.setStart(nodes[startNode], startOffset);
    range.setEnd(nodes[endNode], endOffset + 1);
    results.push({
      index,
      status: "found",
      text: haystack.slice(start, end + 1),
      occurrence_index: wanted ?? 0,
      occurrence_count: count,
      section_index: sectionIndexFor(nodes[startNode]),
      line_rects: mergeLines([...range.getClientRects()]),
      start_path: nodePath(nodes[startNode]),
      start_offset: startOffset,
      end_path: nodePath(nodes[endNode]),
      end_offset: endOffset + 1,
      context_before: haystack.slice(Math.max(0, start - contextChars), start).trim(),
      context_after: haystack.slice(end + 1, end + 1 + contextChars).trim(),
    });
  });
  return { text: document.body.innerText, sections, quotes: results, errors };
}

function fail(err) {
  const message = err instanceof Error ? err.message : String(err);
  process.stderr.write(`${JSON.stringify({ error: message })}\n`);
  process.exit(1);
}

try {
  const { values } = parseArgs({
    options: {
      url: { type: "string" },
      quotes: { type: "string" },
      "tiles-dir": { type: "string" },
      width: { type: "string" },
      height: { type: "string" },
      "timeout-ms": { type: "string" },
      help: { type: "boolean", short: "h" },
    },
    strict: true,
    allowPositionals: false,
  });
  if (values.help) {
    throw new Error("usage: --url <url> --quotes <requests.json> --tiles-dir <dir> [--width N] [--height N] [--timeout-ms N]");
  }
  if (!values.url || !/^https?:\/\//.test(values.url)) throw new Error("--url must be http(s)");
  if (!values.quotes) throw new Error("--quotes <requests.json> is required");
  if (!values["tiles-dir"]) throw new Error("--tiles-dir <dir> is required");
  const width = Number.parseInt(values.width ?? "1280", 10);
  const height = Number.parseInt(values.height ?? "800", 10);
  const timeoutMs = Number.parseInt(values["timeout-ms"] ?? "60000", 10);
  for (const [name, n] of [["width", width], ["height", height], ["timeout-ms", timeoutMs]]) {
    if (!Number.isInteger(n) || n < 1) throw new Error(`--${name} must be a positive integer`);
  }
  const requests = JSON.parse(readFileSync(values.quotes, "utf8"));
  if (!Array.isArray(requests)) throw new Error("--quotes must hold a JSON array of {text, occurrence_index}");
  const tilesDir = path.resolve(values["tiles-dir"]);
  mkdirSync(tilesDir, { recursive: true });

  const browser = await chromium.launch();
  let output;
  try {
    const page = await browser.newPage({
      viewport: { width, height },
      deviceScaleFactor: 1,
      reducedMotion: "reduce",
      colorScheme: "light",
      locale: "en-GB",
      timezoneId: "UTC",
    });
    await page.goto(values.url, { waitUntil: "networkidle", timeout: timeoutMs });
    await page.addStyleTag({ content: FREEZE_CSS });
    await page.evaluate(settlePage);
    await page.waitForLoadState("networkidle", { timeout: timeoutMs });
    const pageHeight = await page.evaluate(settlePage);
    const located = await page.evaluate(locateQuotes, { requests, contextChars: CONTEXT_CHARS });

    const tiles = [];
    for (let index = 0, y = 0; y < pageHeight; index += 1, y += height) {
      const actualY = await page.evaluate((target) => {
        window.scrollTo(0, target);
        return window.scrollY;
      }, y);
      const tilePath = path.join(tilesDir, `tile-${String(index).padStart(4, "0")}.png`);
      const png = await page.screenshot({ fullPage: false });
      writeFileSync(tilePath, png);
      tiles.push({ path: tilePath, y_px: actualY });
    }
    output = { url: values.url, viewport: { width, height }, page_height_px: pageHeight, tiles, ...located };
  } finally {
    await browser.close();
  }
  // Exit from the write callback: process.exit right after a pipe write can truncate the JSON.
  process.stdout.write(`${JSON.stringify(output)}\n`, () => process.exit(output.errors.length > 0 ? 2 : 0));
} catch (err) {
  fail(err);
}
