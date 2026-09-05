/** Local persistence for the keymap, mirroring the theme's: strict-parsed on load, never fatal. */

import { DEFAULT_KEYMAP_STATE, parseKeymapState, type KeymapState } from "./schema";

export const KEYMAP_STORAGE_KEY = "cf.keymap.v1";

function safeStorage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function loadKeymapState(): KeymapState | null {
  const store = safeStorage();
  if (!store) return null;
  const raw = store.getItem(KEYMAP_STORAGE_KEY);
  if (!raw) return null;
  try {
    return parseKeymapState(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function saveKeymapState(state: KeymapState): void {
  const store = safeStorage();
  if (!store) return;
  try {
    store.setItem(KEYMAP_STORAGE_KEY, JSON.stringify(state));
  } catch {
    /* quota or privacy mode: the keymap still works for this session */
  }
}

export function clearKeymapState(): void {
  safeStorage()?.removeItem(KEYMAP_STORAGE_KEY);
}

export function initialKeymapState(): KeymapState {
  return loadKeymapState() ?? DEFAULT_KEYMAP_STATE;
}
