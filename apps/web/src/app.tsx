import { ThemeProvider, type ThemeState } from "@content-factory/web-ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { useMemo } from "react";
import { isApiError } from "./api/client";
import { useSession } from "./api/queries";
import { LocaleProvider } from "./i18n";
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

/** Syncs the theme with the server only while signed in; local storage always works. */
function ThemedRouter({ router, initialTheme }: { router: AppRouter; initialTheme?: ThemeState }) {
  const { data: session } = useSession();
  const sync = useMemo(() => (session ? themeSyncAdapter : null), [session?.account.id]);
  return (
    <ThemeProvider sync={sync} {...(initialTheme ? { initialState: initialTheme } : {})}>
      <LocaleProvider>
        <RouterProvider router={router} />
      </LocaleProvider>
    </ThemeProvider>
  );
}

export interface AppProps {
  router: AppRouter;
  queryClient: QueryClient;
  initialTheme?: ThemeState;
}

export function App({ router, queryClient, initialTheme }: AppProps) {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemedRouter router={router} {...(initialTheme ? { initialTheme } : {})} />
    </QueryClientProvider>
  );
}
