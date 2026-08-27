import { resolveTheme } from "./resolve";
import { DEFAULT_THEME_STATE, themeStateSchema, type ThemeState } from "./schema";

import { THEME_PAINT_KEY, THEME_STORAGE_KEY, themeBootScript } from "./boot";

export { THEME_PAINT_KEY, THEME_STORAGE_KEY, themeBootScript };

/**
 * Pre-computed CSS for instant first paint. When following the system we store both
 * schemes so the boot script can pick without any theme logic.
 */
interface PaintCache {
  light: { vars: Record<string, string>; attrs: Record<string, string>; scheme: string };
  dark: { vars: Record<string, string>; attrs: Record<string, string>; scheme: string };
}

function safeStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function loadThemeState(): ThemeState | null {
  const store = safeStorage();
  if (!store) return null;
  const raw = store.getItem(THEME_STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = themeStateSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function saveThemeState(state: ThemeState): void {
  const store = safeStorage();
  if (!store) return;
  try {
    const light = resolveTheme(state, "light");
    const dark = resolveTheme(state, "dark");
    const cache: PaintCache = {
      light: { vars: light.vars, attrs: light.attrs, scheme: light.scheme },
      dark: { vars: dark.vars, attrs: dark.attrs, scheme: dark.scheme },
    };
    store.setItem(THEME_STORAGE_KEY, JSON.stringify(state));
    store.setItem(THEME_PAINT_KEY, JSON.stringify(cache));
  } catch {
    /* quota or privacy mode: non-fatal */
  }
}

export function clearThemeState(): void {
  const store = safeStorage();
  store?.removeItem(THEME_STORAGE_KEY);
  store?.removeItem(THEME_PAINT_KEY);
}

export function initialThemeState(): ThemeState {
  return loadThemeState() ?? DEFAULT_THEME_STATE;
}
