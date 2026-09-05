import { DEFAULT_THEME_STATE, type ThemeState } from "@content-factory/web-ui";
import { render } from "@testing-library/react";
import { App, createAppQueryClient } from "../src/app";
import { DEFAULT_APP_PREFS, type AppPrefs } from "../src/prefs/schema";
import { createTestRouter } from "../src/router";

export function renderApp(path = "/", theme: ThemeState = DEFAULT_THEME_STATE, prefs: AppPrefs = DEFAULT_APP_PREFS) {
  const queryClient = createAppQueryClient();
  const router = createTestRouter(queryClient, path);
  const utils = render(<App router={router} queryClient={queryClient} initialTheme={theme} initialPrefs={prefs} />);
  return { ...utils, router, queryClient };
}
