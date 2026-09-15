import type { ThemeSyncAdapter } from "@content-factory/web-ui";
import { api } from "../api/client";

/** Server sync for the app theme, transport not validation: the value passes as-is. */
export const themeSyncAdapter: ThemeSyncAdapter = {
  load: () => api.prefs.getTheme().then((r) => r.value),
  save: (state) => api.prefs.putTheme(state),
};
