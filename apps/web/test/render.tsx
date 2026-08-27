import { DEFAULT_THEME_STATE, type ThemeState } from "@content-factory/web-ui";
import { render } from "@testing-library/react";
import { App, createAppQueryClient } from "../src/app";
import { createTestRouter } from "../src/router";

export function renderApp(path = "/", theme: ThemeState = DEFAULT_THEME_STATE) {
  const queryClient = createAppQueryClient();
  const router = createTestRouter(queryClient, path);
  const utils = render(<App router={router} queryClient={queryClient} initialTheme={theme} />);
  return { ...utils, router, queryClient };
}
