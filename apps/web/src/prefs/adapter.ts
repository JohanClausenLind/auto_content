import { parseKeymapState, type KeymapState, type KeymapSyncAdapter } from "@content-factory/web-ui";
import { api } from "../api/client";
import type { PrefsSyncAdapter } from "./PrefsProvider";
import { parseAppPrefs, type AppPrefs } from "./schema";

/**
 * Both ride the generic account preference store (`/v1/prefs/{key}`). Anything the server hands
 * back that no longer fits the schema is treated as absent rather than crashing the shell — the
 * local copy then wins and is pushed back on the next change.
 */

export const KEYMAP_PREF_KEY = "keymap";
export const APP_PREFS_KEY = "appearance";

export const keymapSyncAdapter: KeymapSyncAdapter = {
  load: () => api.prefs.get(KEYMAP_PREF_KEY).then((r): KeymapState | null => parseKeymapState(r.value)),
  save: (state) => api.prefs.put(KEYMAP_PREF_KEY, state),
};

export const prefsSyncAdapter: PrefsSyncAdapter = {
  load: () => api.prefs.get(APP_PREFS_KEY).then((r): AppPrefs | null => parseAppPrefs(r.value)),
  save: (prefs) => api.prefs.put(APP_PREFS_KEY, prefs),
};
