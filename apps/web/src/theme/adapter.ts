import type { ThemeSyncAdapter } from "@content-factory/web-ui";
import { api } from "../api/client";

/** Server sync for the app theme; the ThemeProvider treats failures as non-fatal. */
export const themeSyncAdapter: ThemeSyncAdapter = {
  load: () => api.prefs.getTheme().then((r) => r.value),
  save: (state) => api.prefs.putTheme(state),
};
