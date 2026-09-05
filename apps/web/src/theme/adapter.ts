import type { ThemeSyncAdapter } from "@content-factory/web-ui";
import { api } from "../api/client";

/**
 * Server sync for the app theme; the ThemeProvider treats failures as non-fatal.
 *
 * This is transport, not validation: `/v1/prefs/theme` is a free-form JSON store and a row written
 * by an older build can be missing fields the schema now requires. The value is handed over as-is
 * so the provider can tell a complete row from one it had to repair, and write the repaired one
 * back instead of patching over it on every load.
 */
export const themeSyncAdapter: ThemeSyncAdapter = {
  load: () => api.prefs.getTheme().then((r) => r.value),
  save: (state) => api.prefs.putTheme(state),
};
