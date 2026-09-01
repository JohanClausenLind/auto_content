import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, createRootRouteWithContext, createRoute, createRouter, Outlet, redirect, type RouterHistory } from "@tanstack/react-router";
import { sessionQuery } from "./api/queries";
import {
  AnalyticsPage,
  AssetsPage,
  BrandPage,
  ConnectionsPage,
  HomePage,
  InboxPage,
  NotFoundPage,
  PersonasPage,
  SourcesPage,
  TemplatesPage,
} from "./pages/areas";
import { CalendarPage } from "./pages/CalendarPage";
import { CreatePage } from "./pages/CreatePage";
import { LoginPage } from "./pages/LoginPage";
import { OperationsPage } from "./pages/OperationsPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { parseSettingsSearch, SettingsPage } from "./pages/SettingsPage";
import { Shell } from "./shell/Shell";

export interface RouterContext {
  queryClient: QueryClient;
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
  notFoundComponent: NotFoundPage,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  beforeLoad: async ({ context }) => {
    const session = await context.queryClient.ensureQueryData(sessionQuery);
    if (session) throw redirect({ to: "/" });
  },
  component: LoginPage,
});

const authedRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "_authed",
  beforeLoad: async ({ context, location }) => {
    const session = await context.queryClient.ensureQueryData(sessionQuery);
    if (!session) throw redirect({ to: "/login", search: location.pathname !== "/" ? { next: location.pathname } : {} });
    return { session };
  },
  component: Shell,
});

function page<const P extends string>(path: P, component: () => React.JSX.Element) {
  return createRoute({ getParentRoute: () => authedRoute, path, component });
}

const operationsRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/operations",
  beforeLoad: ({ context }) => {
    if (!context.session.account.is_owner) throw redirect({ to: "/" });
  },
  component: OperationsPage,
});

const runDetailRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/projects/$runId",
  component: RunDetailPage,
});

const settingsRoute = createRoute({
  getParentRoute: () => authedRoute,
  path: "/settings",
  validateSearch: parseSettingsSearch,
  component: SettingsPage,
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  authedRoute.addChildren([
    page("/", HomePage),
    page("/create", CreatePage),
    page("/inbox", InboxPage),
    page("/personas", PersonasPage),
    page("/calendar", CalendarPage),
    page("/projects", ProjectsPage),
    runDetailRoute,
    page("/assets", AssetsPage),
    page("/brand", BrandPage),
    page("/sources", SourcesPage),
    page("/templates", TemplatesPage),
    page("/connections", ConnectionsPage),
    page("/analytics", AnalyticsPage),
    operationsRoute,
    settingsRoute,
  ]),
]);

export function createAppRouter(queryClient: QueryClient, history?: RouterHistory) {
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: "intent",
    scrollRestoration: true,
    ...(history ? { history } : {}),
  });
}

export function createTestRouter(queryClient: QueryClient, initialPath = "/") {
  return createAppRouter(queryClient, createMemoryHistory({ initialEntries: [initialPath] }));
}

export type AppRouter = ReturnType<typeof createAppRouter>;

declare module "@tanstack/react-router" {
  interface Register {
    router: AppRouter;
  }
}
