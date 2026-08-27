import { DEFAULT_PRESET_FOR_SCHEME, findPreset, THEME_PRESETS } from "./presets";
import type { ThemeState } from "./schema";
import { deriveTokens, schemeOf, STATIC_TOKENS, type BaseTokens, type ColorTokenKey, type Scheme } from "./tokens";

export interface ResolvedTheme {
  /** Preset id or custom theme id actually in use. */
  id: string;
  name: string;
  scheme: Scheme;
  base: BaseTokens;
  tokens: Record<ColorTokenKey, string>;
  /** Everything written to `:root`, keyed by CSS custom property name (`--cf-*`). */
  vars: Record<string, string>;
  attrs: Record<string, string>;
}

export interface PreviewTheme {
  base: BaseTokens;
  advanced?: Partial<Record<ColorTokenKey, string>>;
  name?: string;
}

const ROOT_ATTR_THEME = "data-cf-theme";
const ROOT_ATTR_SCHEME = "data-cf-scheme";
const ROOT_ATTR_TRANSPARENCY = "data-cf-reduced-transparency";

export function systemScheme(): Scheme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return "dark";
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

/** Pure: compute the vars a state produces for a given system scheme (and optional live preview). */
export function resolveTheme(state: ThemeState, scheme: Scheme = systemScheme(), preview?: PreviewTheme): ResolvedTheme {
  let id: string;
  let name: string;
  let base: BaseTokens;
  let advanced: Partial<Record<ColorTokenKey, string>> | undefined;

  const custom = state.mode === "custom" ? state.customThemes.find((c) => c.id === state.customId) : undefined;
  if (preview) {
    id = "preview";
    name = preview.name ?? "Preview";
    base = preview.base;
    advanced = preview.advanced;
  } else if (custom) {
    id = custom.id;
    name = custom.name;
    base = custom.base;
    advanced = custom.advanced;
  } else {
    const presetId = state.mode === "system" ? DEFAULT_PRESET_FOR_SCHEME[scheme] : state.preset;
    const preset = findPreset(presetId) ?? THEME_PRESETS[0]!;
    id = preset.id;
    name = preset.name;
    base = preset.base;
    advanced = preset.advanced;
  }

  const tokens = deriveTokens(base, advanced);
  const resolvedScheme = schemeOf(base);
  const vars: Record<string, string> = {};
  for (const [k, v] of Object.entries(tokens)) vars[`--cf-${k}`] = v;
  for (const [k, v] of Object.entries(STATIC_TOKENS)) vars[`--cf-${k}`] = v;
  vars["--cf-scale"] = String(state.scale);
  vars["--cf-panel-alpha"] = state.reducedTransparency ? "1" : "0.86";
  vars["--cf-blur"] = state.reducedTransparency ? "0px" : "10px";

  return {
    id,
    name,
    scheme: resolvedScheme,
    base,
    tokens,
    vars,
    attrs: {
      [ROOT_ATTR_THEME]: id,
      [ROOT_ATTR_SCHEME]: resolvedScheme,
      [ROOT_ATTR_TRANSPARENCY]: String(state.reducedTransparency),
    },
  };
}

/** Write a resolved theme onto `<html>`. */
export function paintTheme(resolved: ResolvedTheme, root: HTMLElement = document.documentElement): void {
  for (const [k, v] of Object.entries(resolved.vars)) root.style.setProperty(k, v);
  for (const [k, v] of Object.entries(resolved.attrs)) root.setAttribute(k, v);
  root.style.colorScheme = resolved.scheme;
}

/** Resolve + paint in one step. Returns what was applied. */
export function applyTheme(state: ThemeState, preview?: PreviewTheme): ResolvedTheme {
  const resolved = resolveTheme(state, systemScheme(), preview);
  if (typeof document !== "undefined") paintTheme(resolved);
  return resolved;
}
