// The CONTENT design system (what viewers see), separate from the operator app theme.
// One theme for now: "editorial" — restrained, high legibility, financial-journalism feel.
// No gradients, no glow, no bounce. Brand tokens may override accent/paper/ink only.
import type { AspectRatio7 as AspectRatio, BrandTokens } from "@content-factory/content-schema-ts";

import { contrastRatio, isDark, mix } from "./color";

export type { AspectRatio };

export type TextRole = "headline" | "subhead" | "body" | "caption" | "label" | "number" | "source";
export type BackgroundRole = "paper" | "ink" | "surface";
export type SeriesIndex = 1 | 2 | 3 | 4 | 5 | 6;
export type ColorRole =
  | BackgroundRole
  | "accent"
  | "muted"
  | "rule"
  | `series-${SeriesIndex}`;

export interface ColorTokens {
  paper: string;
  ink: string;
  surface: string;
  accent: string;
  muted: string;
  /** Hairline rules and dividers (not for text). */
  rule: string;
  /** Text colours when the background is `ink`. */
  onInk: { text: string; muted: string; accent: string; rule: string; surface: string };
  /** Colour-blind-safe categorical palette (Okabe–Ito ordering). */
  series: readonly [string, string, string, string, string, string];
  tone: { neutral: string; warning: string; positive: string };
}

export interface TypeStyle {
  /** Preferred size in px at the 1080 reference short side. */
  size: number;
  /** Smallest size the fitter may fall back to (px at 1080). */
  min: number;
  weight: 400 | 500 | 600 | 700 | 800;
  /** Pinned family the role is set in (Inter unless a brand opts display roles into Sora). */
  family: "Inter" | "Sora";
  lineHeight: number;
  /** In em. */
  letterSpacing: number;
  uppercase: boolean;
  tabular: boolean;
}

export interface GridSpec {
  width: number;
  height: number;
  columns: number;
  /** px at the aspect's native size. */
  gutter: number;
  margin: number;
  /** Normalized safe area (keeps clear of platform UI chrome). */
  safe: { x: number; y: number; w: number; h: number };
}

export interface SeriesStyle {
  color: string;
  /** SVG dash pattern; redundancy so series never rely on hue alone. */
  dash: string;
  strokeWidth: number;
  pointRadius: number;
  areaOpacity: number;
}

export interface MotionTokens {
  /** Durations in milliseconds; converted to frames by the video layer. */
  duration: { micro: number; fast: number; base: number; slow: number; countUp: number };
  /** Cubic-bezier control points. */
  easing: {
    standard: readonly [number, number, number, number];
    decelerate: readonly [number, number, number, number];
    accelerate: readonly [number, number, number, number];
    linear: readonly [number, number, number, number];
  };
  /** Overdamped: settles without overshoot (no bounce). */
  spring: { damping: number; stiffness: number; mass: number };
}

export interface ContentTheme {
  name: string;
  version: string;
  fontFamily: "Inter";
  color: ColorTokens;
  type: Record<TextRole | "display", TypeStyle>;
  /** 4px-based spacing scale, px at 1080. */
  space: readonly number[];
  radius: { none: number; sm: number; md: number; lg: number; full: number };
  grid: Record<AspectRatio, GridSpec>;
  series: readonly SeriesStyle[];
  motion: MotionTokens;
  citation: { prefix: string; separator: string; role: TextRole; showAccessed: boolean };
  caption: { role: TextRole; color: "muted" | "ink" };
  legibility: { minFontPx1080: number; minContrast: number; maxLines: number };
}

const OKABE_ITO = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9"] as const;
const DASHES = ["", "10 7", "2 6", "14 5 3 5", "6 4", "1 5"] as const;

export const editorialTheme: ContentTheme = {
  name: "editorial",
  version: "1.0.0",
  fontFamily: "Inter",
  color: {
    paper: "#FAF8F3",
    ink: "#14171C",
    surface: "#EDEAE2",
    accent: "#0B5FA5",
    muted: "#525A66",
    rule: "#C9C4B8",
    onInk: { text: "#FAF8F3", muted: "#AEB6C2", accent: "#8BBDEB", rule: "#3A4049", surface: "#22262D" },
    series: OKABE_ITO,
    tone: { neutral: "#525A66", warning: "#B2551A", positive: "#1B7F4C" },
  },
  type: {
    display: { size: 96, min: 48, weight: 700, family: "Inter", lineHeight: 1.05, letterSpacing: -0.02, uppercase: false, tabular: false },
    headline: { size: 60, min: 32, weight: 700, family: "Inter", lineHeight: 1.1, letterSpacing: -0.015, uppercase: false, tabular: false },
    subhead: { size: 38, min: 26, weight: 500, family: "Inter", lineHeight: 1.2, letterSpacing: -0.005, uppercase: false, tabular: false },
    body: { size: 30, min: 24, weight: 400, family: "Inter", lineHeight: 1.35, letterSpacing: 0, uppercase: false, tabular: false },
    caption: { size: 24, min: 24, weight: 400, family: "Inter", lineHeight: 1.35, letterSpacing: 0, uppercase: false, tabular: false },
    label: { size: 26, min: 24, weight: 600, family: "Inter", lineHeight: 1.2, letterSpacing: 0.08, uppercase: true, tabular: false },
    number: { size: 220, min: 48, weight: 700, family: "Inter", lineHeight: 1.0, letterSpacing: -0.03, uppercase: false, tabular: true },
    source: { size: 24, min: 24, weight: 400, family: "Inter", lineHeight: 1.3, letterSpacing: 0, uppercase: false, tabular: false },
  },
  space: [0, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128],
  radius: { none: 0, sm: 4, md: 12, lg: 24, full: 9999 },
  grid: {
    "16:9": { width: 1920, height: 1080, columns: 12, gutter: 24, margin: 96, safe: { x: 0.05, y: 0.06, w: 0.9, h: 0.88 } },
    "9:16": { width: 1080, height: 1920, columns: 6, gutter: 20, margin: 72, safe: { x: 0.07, y: 0.12, w: 0.86, h: 0.7 } },
    "1:1": { width: 1080, height: 1080, columns: 8, gutter: 20, margin: 72, safe: { x: 0.06, y: 0.06, w: 0.88, h: 0.88 } },
    "4:5": { width: 1080, height: 1350, columns: 8, gutter: 20, margin: 72, safe: { x: 0.06, y: 0.07, w: 0.88, h: 0.86 } },
  },
  series: OKABE_ITO.map((color, i) => ({
    color,
    dash: DASHES[i] ?? "",
    strokeWidth: 4,
    pointRadius: 5,
    areaOpacity: 0.18,
  })),
  motion: {
    duration: { micro: 120, fast: 200, base: 360, slow: 600, countUp: 1400 },
    easing: {
      standard: [0.2, 0, 0, 1],
      decelerate: [0, 0, 0.2, 1],
      accelerate: [0.4, 0, 1, 1],
      linear: [0, 0, 1, 1],
    },
    spring: { damping: 200, stiffness: 120, mass: 1 },
  },
  citation: { prefix: "Source", separator: " · ", role: "source", showAccessed: false },
  caption: { role: "caption", color: "muted" },
  legibility: { minFontPx1080: 24, minContrast: 4.5, maxLines: 6 },
};

export const THEMES: Readonly<Record<string, ContentTheme>> = { editorial: editorialTheme };

/** Unknown theme names fall back to editorial so any valid spec renders. */
export function themeByName(name: string): ContentTheme {
  return THEMES[name] ?? editorialTheme;
}

/** Apply approved brand overrides (accent/paper/ink only) and re-derive dependent colours. */
export function applyBrand(theme: ContentTheme, brand: BrandTokens | null | undefined): ContentTheme {
  if (!brand) return theme;
  const withType = brand.font_family === "Sora" ? withDisplayFamily(theme, "Sora") : theme;
  if (brand.accent === null && brand.paper === null && brand.ink === null) return withType;
  theme = withType;
  const paper = brand.paper ?? theme.color.paper;
  const ink = brand.ink ?? theme.color.ink;
  const accent = brand.accent ?? theme.color.accent;
  const derived = brand.paper !== null || brand.ink !== null;
  // A dark paper (a night-mode brand) flips the roles: `textColor` reads `isDark(paper)` and then
  // takes the `onInk` colours, so those must be "on a dark ground" colours — light text, a light
  // muted, the accent nudged towards the ink — not the paper colour itself, which would vanish.
  const darkPaper = isDark(paper);
  const onDark = {
    text: ink,
    muted: mix(ink, paper, 0.3),
    accent: mix(accent, ink, 0.15),
    rule: mix(paper, ink, 0.25),
    surface: mix(paper, ink, 0.06),
  };
  return {
    ...theme,
    color: {
      ...theme.color,
      paper,
      ink,
      accent,
      surface: derived ? mix(paper, ink, 0.06) : theme.color.surface,
      muted: derived ? mix(ink, paper, 0.3) : theme.color.muted,
      rule: derived ? mix(paper, ink, 0.25) : theme.color.rule,
      onInk: darkPaper
        ? onDark
        : {
            text: paper,
            muted: derived ? mix(paper, ink, 0.3) : theme.color.onInk.muted,
            accent: brand.accent !== null ? mix(accent, paper, 0.5) : theme.color.onInk.accent,
            rule: derived ? mix(ink, paper, 0.18) : theme.color.onInk.rule,
            surface: derived ? mix(ink, paper, 0.06) : theme.color.onInk.surface,
          },
    },
  };
}

/** Set the display roles (display, headline, number) in another pinned family. Sora carries an
 * 800 weight, so the figure and the display line step up to it; tracking tightens to match. */
export function withDisplayFamily(theme: ContentTheme, family: "Inter" | "Sora"): ContentTheme {
  if (family === "Inter") return theme;
  const t = theme.type;
  return {
    ...theme,
    type: {
      ...t,
      display: { ...t.display, family, weight: 800, letterSpacing: -0.03 },
      headline: { ...t.headline, family, weight: 700, letterSpacing: -0.02 },
      number: { ...t.number, family, weight: 800, letterSpacing: -0.04 },
    },
  };
}

export function resolveTheme(name: string, brand: BrandTokens | null | undefined): ContentTheme {
  return applyBrand(themeByName(name), brand);
}

/** Size multiplier relative to the 1080 reference short side. */
export function scaleFor(width: number, height: number): number {
  return Math.min(width, height) / 1080;
}

export function backgroundColor(theme: ContentTheme, role: BackgroundRole): string {
  return theme.color[role];
}

/** Foreground colour for a text role on a background role; contrast is asserted by tests/QC. */
export function textColor(theme: ContentTheme, role: TextRole, background: BackgroundRole): string {
  const dark = background === "ink" || isDark(theme.color[background]);
  const c = theme.color;
  switch (role) {
    case "label":
      return dark ? c.onInk.accent : c.accent;
    case "caption":
    case "source":
      return dark ? c.onInk.muted : c.muted;
    default:
      return dark ? c.onInk.text : c.ink;
  }
}

export function colorByRole(theme: ContentTheme, role: ColorRole, background: BackgroundRole = "paper"): string {
  const dark = background === "ink";
  switch (role) {
    case "paper":
    case "ink":
    case "accent":
      return theme.color[role];
    case "surface":
      return dark ? theme.color.onInk.surface : theme.color.surface;
    case "muted":
      return dark ? theme.color.onInk.muted : theme.color.muted;
    case "rule":
      return dark ? theme.color.onInk.rule : theme.color.rule;
    default: {
      const idx = Number(role.slice("series-".length)) - 1;
      return theme.color.series[idx] ?? theme.color.series[0];
    }
  }
}

export const TEXT_ROLES: readonly TextRole[] = ["headline", "subhead", "body", "caption", "label", "number", "source"];
export const BACKGROUND_ROLES: readonly BackgroundRole[] = ["paper", "ink", "surface"];

export interface ContrastCheck {
  role: TextRole;
  background: BackgroundRole;
  foreground: string;
  backgroundColor: string;
  ratio: number;
  pass: boolean;
}

/** Every text role against every background role. */
export function contrastMatrix(theme: ContentTheme): ContrastCheck[] {
  const out: ContrastCheck[] = [];
  for (const background of BACKGROUND_ROLES) {
    const bg = backgroundColor(theme, background);
    for (const role of TEXT_ROLES) {
      const fg = textColor(theme, role, background);
      const ratio = contrastRatio(fg, bg);
      out.push({ role, background, foreground: fg, backgroundColor: bg, ratio, pass: ratio >= theme.legibility.minContrast });
    }
  }
  return out;
}

/** CSS custom properties for the theme (`--cf-…`), for previews and non-React consumers. */
export function themeToCssVars(theme: ContentTheme): Record<string, string> {
  const v: Record<string, string> = {
    "--cf-font-family": `"${theme.fontFamily}"`,
    "--cf-color-paper": theme.color.paper,
    "--cf-color-ink": theme.color.ink,
    "--cf-color-surface": theme.color.surface,
    "--cf-color-accent": theme.color.accent,
    "--cf-color-muted": theme.color.muted,
    "--cf-color-rule": theme.color.rule,
    "--cf-radius-sm": `${theme.radius.sm}px`,
    "--cf-radius-md": `${theme.radius.md}px`,
    "--cf-radius-lg": `${theme.radius.lg}px`,
    "--cf-motion-fast": `${theme.motion.duration.fast}ms`,
    "--cf-motion-base": `${theme.motion.duration.base}ms`,
    "--cf-motion-slow": `${theme.motion.duration.slow}ms`,
    "--cf-ease-standard": `cubic-bezier(${theme.motion.easing.standard.join(",")})`,
  };
  theme.color.series.forEach((c, i) => {
    v[`--cf-color-series-${i + 1}`] = c;
  });
  for (const [role, t] of Object.entries(theme.type)) {
    v[`--cf-type-${role}-size`] = `${t.size}px`;
    v[`--cf-type-${role}-weight`] = String(t.weight);
    v[`--cf-type-${role}-line-height`] = String(t.lineHeight);
  }
  theme.space.forEach((s, i) => {
    v[`--cf-space-${i}`] = `${s}px`;
  });
  return v;
}

export function cssVarsText(theme: ContentTheme, selector = ":root"): string {
  const body = Object.entries(themeToCssVars(theme))
    .map(([k, val]) => `${k}:${val};`)
    .join("");
  return `${selector}{${body}}`;
}
