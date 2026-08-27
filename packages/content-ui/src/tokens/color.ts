// Colour math (sRGB, WCAG 2.x). Pure functions; no rounding surprises across runtimes.

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

const HEX = /^#?([0-9a-f]{6})$/i;

export function parseHex(hex: string): Rgb {
  const m = HEX.exec(hex.trim());
  if (!m || m[1] === undefined) throw new Error(`Unsupported colour "${hex}" (expected #rrggbb)`);
  const v = Number.parseInt(m[1], 16);
  return { r: (v >> 16) & 0xff, g: (v >> 8) & 0xff, b: v & 0xff };
}

export function toHex({ r, g, b }: Rgb): string {
  const c = (n: number) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

/** WCAG relative luminance in [0, 1]. */
export function luminance(hex: string): number {
  const { r, g, b } = parseHex(hex);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG contrast ratio (>= 1). Symmetric in its arguments. */
export function contrastRatio(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** Linear mix in sRGB space: t = 0 -> a, t = 1 -> b. */
export function mix(a: string, b: string, t: number): string {
  const A = parseHex(a);
  const B = parseHex(b);
  return toHex({ r: A.r + (B.r - A.r) * t, g: A.g + (B.g - A.g) * t, b: A.b + (B.b - A.b) * t });
}

export function isDark(hex: string): boolean {
  return luminance(hex) < 0.18;
}
