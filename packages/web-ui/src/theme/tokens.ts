import { bestForeground, darken, isDark, lighten, mix } from "./color";

/** The small set an author writes. Everything else is derived. */
export interface BaseTokens {
  bg: string;
  fg: string;
  panel: string;
  border: string;
  accent: string;
}

export const BASE_TOKEN_KEYS = ["bg", "fg", "panel", "border", "accent"] as const satisfies readonly (keyof BaseTokens)[];

/** Derived colour tokens (CSS var name without the `--cf-` prefix). */
export const DERIVED_TOKEN_KEYS = [
  "accent-fg",
  "accent-hover",
  "accent-soft",
  "danger",
  "danger-soft",
  "success",
  "success-soft",
  "warn",
  "warn-soft",
  "muted",
  "panel-2",
  "hover",
  "border-strong",
  "focus",
  "overlay",
  "shadow-sm",
  "shadow-md",
  "shadow-lg",
] as const;

export type DerivedTokenKey = (typeof DERIVED_TOKEN_KEYS)[number];
export type ColorTokenKey = keyof BaseTokens | DerivedTokenKey;

/** Tokens that never change between themes (only `--cf-scale` is user-adjustable). */
export const STATIC_TOKENS: Readonly<Record<string, string>> = {
  "radius-sm": "4px",
  "radius-md": "8px",
  "radius-lg": "14px",
  "radius-full": "999px",
  "space-1": "0.25rem",
  "space-2": "0.5rem",
  "space-3": "0.75rem",
  "space-4": "1rem",
  "space-5": "1.5rem",
  "space-6": "2rem",
  "space-7": "3rem",
  "space-8": "4rem",
  "font-sans":
    'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif',
  "font-mono": 'ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace',
  "duration": "150ms",
};

export type Scheme = "light" | "dark";

/** Semantic hue seeds; adjusted toward the scheme so they stay legible. */
const SEEDS = { danger: "#e5484d", success: "#2f9e5a", warn: "#d99a1e" } as const;

function tune(seed: string, scheme: Scheme): string {
  return scheme === "dark" ? mix(seed, "#ffffff", 0.22) : mix(seed, "#000000", 0.18);
}

export function schemeOf(base: BaseTokens): Scheme {
  return isDark(base.bg) ? "dark" : "light";
}

/** Derive the full colour token map from the base set (plus optional advanced overrides). */
export function deriveTokens(
  base: BaseTokens,
  advanced: Readonly<Partial<Record<ColorTokenKey, string>>> = {},
): Record<ColorTokenKey, string> {
  const scheme = schemeOf(base);
  const dark = scheme === "dark";
  const { bg, fg, panel, border, accent } = base;
  const shadowInk = dark ? "0 0 0" : "16 18 24";
  const derived: Record<ColorTokenKey, string> = {
    bg,
    fg,
    panel,
    border,
    accent,
    "accent-fg": bestForeground(accent),
    "accent-hover": dark ? lighten(accent, 0.07) : darken(accent, 0.07),
    "accent-soft": mix(bg, accent, 0.18),
    danger: tune(SEEDS.danger, scheme),
    "danger-soft": mix(bg, SEEDS.danger, 0.16),
    success: tune(SEEDS.success, scheme),
    "success-soft": mix(bg, SEEDS.success, 0.16),
    warn: tune(SEEDS.warn, scheme),
    "warn-soft": mix(bg, SEEDS.warn, 0.16),
    muted: mix(bg, fg, 0.66),
    "panel-2": dark ? lighten(panel, 0.04) : darken(panel, 0.03),
    hover: mix(panel, fg, 0.08),
    "border-strong": mix(border, fg, 0.3),
    focus: accent,
    overlay: dark ? "rgb(0 0 0 / 0.6)" : "rgb(20 22 28 / 0.35)",
    "shadow-sm": `0 1px 2px rgb(${shadowInk} / ${dark ? 0.5 : 0.08})`,
    "shadow-md": `0 4px 12px rgb(${shadowInk} / ${dark ? 0.55 : 0.12})`,
    "shadow-lg": `0 16px 40px rgb(${shadowInk} / ${dark ? 0.65 : 0.18})`,
  };
  for (const [key, value] of Object.entries(advanced)) {
    if (value !== undefined) derived[key as ColorTokenKey] = value;
  }
  return derived;
}
