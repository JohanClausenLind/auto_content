/**
 * Operator preferences that are not the theme and not the keymap: how dense the shell is, how much
 * it animates, and where a session starts. Colour lives in the theme engine; this is everything
 * else that is a matter of taste rather than data.
 */

import { z } from "zod";

export const APP_PREFS_VERSION = 1;

export const appPrefsSchema = z.object({
  version: z.literal(APP_PREFS_VERSION),
  /** Row height and padding across the shell. */
  density: z.enum(["comfortable", "compact"]),
  /** "system" follows prefers-reduced-motion; the others override it in either direction. */
  reducedMotion: z.enum(["system", "always", "never"]),
  /** Start with the area rail collapsed to icons. */
  navCollapsed: z.boolean(),
  /** Route to open after signing in — a path from the nav areas. */
  landingArea: z.string().min(1).max(64),
  /** Workspace to select on sign-in; null keeps whatever the server last had. */
  defaultWorkspaceId: z.string().min(1).max(64).nullable(),
});

export type AppPrefs = z.infer<typeof appPrefsSchema>;

export const DEFAULT_APP_PREFS: AppPrefs = {
  version: APP_PREFS_VERSION,
  density: "comfortable",
  reducedMotion: "system",
  navCollapsed: false,
  landingArea: "/",
  defaultWorkspaceId: null,
};

export function parseAppPrefs(value: unknown): AppPrefs | null {
  const parsed = appPrefsSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}

export const APP_PREFS_STORAGE_KEY = "cf.prefs.v1";

function safeStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function loadAppPrefs(): AppPrefs | null {
  const store = safeStorage();
  if (!store) return null;
  const raw = store.getItem(APP_PREFS_STORAGE_KEY);
  if (!raw) return null;
  try {
    return parseAppPrefs(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function saveAppPrefs(prefs: AppPrefs): void {
  try {
    safeStorage()?.setItem(APP_PREFS_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    /* quota or privacy mode: non-fatal */
  }
}

export function initialAppPrefs(): AppPrefs {
  return loadAppPrefs() ?? DEFAULT_APP_PREFS;
}
