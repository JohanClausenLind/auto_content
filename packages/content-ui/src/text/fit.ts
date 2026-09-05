// Deterministic text fitting. Widths come from the pinned Inter advance table (no canvas, no DOM),
// so Node, the headless browser and tests agree byte-for-byte. Kerning is ignored, which only
// makes the estimate wider than reality; a small safety factor covers hinting/rounding.
import { INTER_WIDTHS } from "../fonts/inter-widths";
import { SORA_WIDTHS } from "../fonts/sora-widths";
import type { FontFamily, FontWeight } from "../fonts";

/** Advance-width table for a family/weight; unknown combinations fall back to the nearest Inter weight. */
function widthTable(family: FontFamily | undefined, weight: FontWeight): Readonly<Record<string, number>> {
  if (family === "Sora") {
    const t = (SORA_WIDTHS as Record<number, Readonly<Record<string, number>>>)[weight];
    if (t) return t;
  }
  const inter = INTER_WIDTHS as Record<number, Readonly<Record<string, number>>>;
  return inter[weight] ?? inter[Math.min(700, Math.max(400, weight))] ?? INTER_WIDTHS[700];
}

/** Multiplier applied to measured widths so a line that "fits" never touches the frame edge. */
export const FIT_SAFETY = 1.015;
/** Fallback advance (1/1000 em) for code points outside the table (e.g. CJK, emoji). */
const FALLBACK_ADVANCE = 1000;
const ELLIPSIS = "…";

export interface MeasureOptions {
  weight: FontWeight;
  /** Pinned family the text is set in; Inter when omitted. */
  family?: FontFamily;
  fontSize: number;
  /** In em. */
  letterSpacing?: number;
}

/** Estimated advance width of `text` in px (without safety factor). */
export function measureText(text: string, { weight, fontSize, letterSpacing = 0, family }: MeasureOptions): number {
  const table = widthTable(family, weight);
  let units = 0;
  let count = 0;
  for (const ch of text) {
    const adv = table[ch];
    units += adv === undefined ? fallbackAdvance(ch, table) : adv;
    count += 1;
  }
  return (units / 1000) * fontSize + Math.max(0, count - 1) * letterSpacing * fontSize;
}

function fallbackAdvance(ch: string, table: Readonly<Record<string, number>>): number {
  // Combining marks take no space; digits-like and letters fall back to the table average.
  const cp = ch.codePointAt(0) ?? 0;
  if (cp >= 0x0300 && cp <= 0x036f) return 0;
  if (cp === 0x200b) return 0;
  if (/\p{L}|\p{N}/u.test(ch)) return table["n"] ?? 560;
  return FALLBACK_ADVANCE;
}

export interface WrapOptions extends MeasureOptions {
  maxWidth: number;
}

/** Greedy word wrap; words wider than the frame are hard-broken by character. */
export function wrapText(text: string, opts: WrapOptions): string[] {
  const width = (s: string) => measureText(s, opts) * FIT_SAFETY;
  const paragraphs = text.replace(/\r\n?/g, "\n").split("\n");
  const lines: string[] = [];
  for (const para of paragraphs) {
    const words = para.split(/\s+/).filter((w) => w.length > 0);
    if (words.length === 0) {
      lines.push("");
      continue;
    }
    let current = "";
    for (const word of words) {
      const candidate = current.length === 0 ? word : `${current} ${word}`;
      if (width(candidate) <= opts.maxWidth) {
        current = candidate;
        continue;
      }
      if (current.length > 0) lines.push(current);
      if (width(word) <= opts.maxWidth) {
        current = word;
        continue;
      }
      // Hard-break an over-long word.
      let chunk = "";
      for (const ch of word) {
        if (width(chunk + ch) <= opts.maxWidth || chunk.length === 0) chunk += ch;
        else {
          lines.push(chunk);
          chunk = ch;
        }
      }
      current = chunk;
    }
    lines.push(current);
  }
  return lines;
}

export interface FitOptions {
  text: string;
  weight: FontWeight;
  /** Pinned family the text is set in; Inter when omitted. */
  family?: FontFamily;
  maxWidth: number;
  maxHeight: number;
  maxLines: number;
  /** Starting (largest) size in px. */
  preferredSize: number;
  /** Smallest size in px the fitter may choose before truncating. */
  minSize: number;
  lineHeight: number;
  letterSpacing?: number;
  /** Size decrement in px (integer sizes keep layout stable). */
  step?: number;
}

export interface FitResult {
  fontSize: number;
  lines: string[];
  lineHeightPx: number;
  /** Widest line in px (with safety factor). */
  widthPx: number;
  heightPx: number;
  /** True when even `minSize` could not hold the text; lines were cut with an ellipsis. */
  truncated: boolean;
}

/**
 * Largest integer font size in [minSize, preferredSize] for which the wrapped text fits both
 * `maxLines` and `maxHeight`; otherwise `minSize` with deterministic ellipsis truncation.
 */
export function fitText(opts: FitOptions): FitResult {
  const step = Math.max(1, Math.floor(opts.step ?? 2));
  const start = Math.max(1, Math.floor(opts.preferredSize));
  const min = Math.max(1, Math.min(start, Math.floor(opts.minSize)));
  const attempt = (fontSize: number): FitResult => {
    const measure: MeasureOptions = { weight: opts.weight, fontSize, letterSpacing: opts.letterSpacing ?? 0, ...(opts.family ? { family: opts.family } : {}) };
    const lines = wrapText(opts.text, { ...measure, maxWidth: opts.maxWidth });
    const lineHeightPx = fontSize * opts.lineHeight;
    return {
      fontSize,
      lines,
      lineHeightPx,
      widthPx: Math.max(0, ...lines.map((l) => measureText(l, measure) * FIT_SAFETY)),
      heightPx: lines.length * lineHeightPx,
      truncated: false,
    };
  };
  const fits = (r: FitResult) =>
    r.lines.length <= opts.maxLines && r.heightPx <= opts.maxHeight + 1e-6 && r.widthPx <= opts.maxWidth + 1e-6;

  for (let size = start; size >= min; size -= step) {
    const r = attempt(size);
    if (fits(r)) return r;
  }
  if (min !== start && (start - min) % step !== 0) {
    const r = attempt(min);
    if (fits(r)) return r;
  }
  return truncate(attempt(min), opts);
}

function truncate(r: FitResult, opts: FitOptions): FitResult {
  const measure: MeasureOptions = { weight: opts.weight, fontSize: r.fontSize, letterSpacing: opts.letterSpacing ?? 0, ...(opts.family ? { family: opts.family } : {}) };
  const width = (s: string) => measureText(s, measure) * FIT_SAFETY;
  const roomLines = Math.max(1, Math.min(opts.maxLines, Math.floor(opts.maxHeight / r.lineHeightPx)));
  const lines = r.lines.slice(0, roomLines);
  const lastIdx = lines.length - 1;
  let last = lines[lastIdx] ?? "";
  const cut = lines.length < r.lines.length || last !== r.lines[lastIdx];
  if (cut || width(last) > opts.maxWidth) {
    const chars = [...last];
    while (chars.length > 0 && width(chars.join("") + ELLIPSIS) > opts.maxWidth) chars.pop();
    last = chars.join("").replace(/\s+$/, "") + ELLIPSIS;
    lines[lastIdx] = last;
  }
  return {
    ...r,
    lines,
    widthPx: Math.max(0, ...lines.map(width)),
    heightPx: lines.length * r.lineHeightPx,
    truncated: true,
  };
}

export interface FitNumberOptions {
  numeral: string;
  unit: string;
  weight: FontWeight;
  /** Pinned family the text is set in; Inter when omitted. */
  family?: FontFamily;
  maxWidth: number;
  maxHeight: number;
  preferredSize: number;
  minSize: number;
  lineHeight: number;
  letterSpacing?: number;
  /** Unit size relative to the numeral. */
  unitScale?: number;
  /** Gap between numeral and unit in em of the numeral. */
  unitGap?: number;
}

export interface FitNumberResult {
  fontSize: number;
  unitFontSize: number;
  widthPx: number;
  heightPx: number;
  /** True if the minimum size still overflows (the renderer clips; QC flags it). */
  overflow: boolean;
}

/** Single-line numeral + smaller unit; largest integer size that fits width and height. */
export function fitNumber(opts: FitNumberOptions): FitNumberResult {
  const unitScale = opts.unitScale ?? 0.4;
  const unitGap = opts.unitGap ?? 0.06;
  const start = Math.max(1, Math.floor(opts.preferredSize));
  const min = Math.max(1, Math.min(start, Math.floor(opts.minSize)));
  const widthAt = (size: number) => {
    const n = measureText(opts.numeral, { weight: opts.weight, fontSize: size, letterSpacing: opts.letterSpacing ?? 0, ...(opts.family ? { family: opts.family } : {}) });
    const u = opts.unit.length === 0 ? 0 : measureText(opts.unit, { weight: opts.weight, fontSize: size * unitScale, ...(opts.family ? { family: opts.family } : {}) }) + size * unitGap;
    return (n + u) * FIT_SAFETY;
  };
  // Width and height are monotone in size: solve directly, then snap to an integer.
  let size = start;
  const heightCap = Math.floor(opts.maxHeight / opts.lineHeight);
  size = Math.min(size, Math.max(min, heightCap));
  const w0 = widthAt(size);
  if (w0 > opts.maxWidth) size = Math.max(min, Math.floor((size * opts.maxWidth) / w0));
  while (size > min && widthAt(size) > opts.maxWidth) size -= 1;
  const widthPx = widthAt(size);
  const heightPx = size * opts.lineHeight;
  return {
    fontSize: size,
    unitFontSize: Math.round(size * unitScale),
    widthPx,
    heightPx,
    overflow: widthPx > opts.maxWidth + 1e-6 || heightPx > opts.maxHeight + 1e-6,
  };
}
