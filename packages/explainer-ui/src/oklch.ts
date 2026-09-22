// OKLCH → sRGB at render time, with chroma reduced by bisection when a token is outside sRGB.
export type Oklch = readonly [lightness: number, chroma: number, hueDeg: number];

function oklabToLinearSrgb(L: number, a: number, b: number): [number, number, number] {
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3;
  const m = m_ ** 3;
  const s = s_ ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

function linearOf([L, C, h]: Oklch): [number, number, number] {
  const rad = (h * Math.PI) / 180;
  return oklabToLinearSrgb(L, C * Math.cos(rad), C * Math.sin(rad));
}

function inGamut(rgb: readonly number[], tolerance = 1e-6): boolean {
  return rgb.every((c) => c >= -tolerance && c <= 1 + tolerance);
}

function gamma(v: number): number {
  return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
}

/** sRGB triple 0..255 for an OKLCH token; identical to the Python `oklch_to_srgb` (parity test). */
export function oklchToRgb(color: Oklch): [number, number, number] {
  let rgb = linearOf(color);
  if (!inGamut(rgb)) {
    let lo = 0;
    let hi = color[1];
    for (let i = 0; i < 32; i += 1) {
      const mid = (lo + hi) / 2;
      if (inGamut(linearOf([color[0], mid, color[2]]))) lo = mid;
      else hi = mid;
    }
    rgb = linearOf([color[0], lo, color[2]]);
  }
  return rgb.map((c) => Math.round(255 * Math.min(1, Math.max(0, gamma(c))))) as [number, number, number];
}

export function rgbToHex([r, g, b]: readonly [number, number, number]): string {
  return `#${[r, g, b].map((c) => c.toString(16).toUpperCase().padStart(2, "0")).join("")}`;
}

export function oklchToHex(color: Oklch): string {
  return rgbToHex(oklchToRgb(color));
}

/** `rgba()` for a token with an alpha multiplier, used by de-emphasis and overlays. */
export function oklchToRgba(color: Oklch, alpha: number): string {
  const [r, g, b] = oklchToRgb(color);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
