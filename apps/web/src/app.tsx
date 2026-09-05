import { ThemeProvider, type ThemeState } from "@content-factory/web-ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useMemo } from "react";
import { isApiError } from "./api/client";
import { useSession } from "./api/queries";
import { LocaleProvider } from "./i18n";
import { PrefsProvider } from "./prefs/PrefsProvider";
import { prefsSyncAdapter } from "./prefs/adapter";
import type { AppPrefs } from "./prefs/schema";
import type { AppRouter } from "./router";
import { themeSyncAdapter } from "./theme/adapter";

export function createAppQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (count, err) => count < 2 && !(isApiError(err) && err.status > 0 && err.status < 500),
        refetchOnWindowFocus: false,
      },
    },
  });
}

/**
 * Syncs the theme and preferences with the server only while signed in; local storage always
 * works. The keymap provider lives inside the shell, where the actions it dispatches exist.
 */
function ThemedRouter({ router, initialTheme, initialPrefs }: { router: AppRouter; initialTheme?: ThemeState; initialPrefs?: AppPrefs }) {
  const { data: session } = useSession();
  const accountId = session?.account.id;
  const themeSync = useMemo(() => (accountId ? themeSyncAdapter : null), [accountId]);
  const prefsSync = useMemo(() => (accountId ? prefsSyncAdapter : null), [accountId]);
  return (
    <ThemeProvider sync={themeSync} {...(initialTheme ? { initialState: initialTheme } : {})}>
      <PrefsProvider sync={prefsSync} {...(initialPrefs ? { initialState: initialPrefs } : {})}>
        <LocaleProvider>
          <RouterProvider router={router} />
        </LocaleProvider>
      </PrefsProvider>
    </ThemeProvider>
  );
}

export interface AppProps {
  router: AppRouter;
  queryClient: QueryClient;
  initialTheme?: ThemeState;
  initialPrefs?: AppPrefs;
}

export function App({ router, queryClient, initialTheme, initialPrefs }: AppProps) {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemedRouter router={router} {...(initialTheme ? { initialTheme } : {})} {...(initialPrefs ? { initialPrefs } : {})} />
    </QueryClientProvider>
  );
}
