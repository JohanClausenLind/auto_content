import { queryOptions, useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, isApiError } from "./client";
import type { Session } from "./types";

export const queryKeys = {
  session: ["session"] as const,
  meta: ["meta"] as const,
  workspaces: ["workspaces"] as const,
  runs: ["runs"] as const,
  run: (runId: string) => ["runs", runId] as const,
  actionItems: ["action-items", "open"] as const,
  matrix: ["campaigns", "matrix"] as const,
  opsHealth: ["operations", "health"] as const,
  opsAudit: ["operations", "audit"] as const,
};

/** How often the run views poll while open. */
export const RUN_POLL_MS = 2000;

/** Resolves to null on 401 so callers can branch without try/catch. */
export const sessionQuery = queryOptions({
  queryKey: queryKeys.session,
  queryFn: async (): Promise<Session | null> => {
    try {
      return await api.session.get();
    } catch (e) {
      if (isApiError(e) && e.status === 401) return null;
      throw e;
    }
  },
  staleTime: 60_000,
  retry: false,
});

export const metaQuery = queryOptions({
  queryKey: queryKeys.meta,
  queryFn: api.meta.get,
  staleTime: 5 * 60_000,
});

export const runsQuery = queryOptions({
  queryKey: queryKeys.runs,
  queryFn: api.runs.list,
  refetchInterval: RUN_POLL_MS,
});

export const runQuery = (runId: string) =>
  queryOptions({
    queryKey: queryKeys.run(runId),
    queryFn: () => api.runs.get(runId),
    refetchInterval: RUN_POLL_MS,
  });

export const actionItemsQuery = queryOptions({
  queryKey: queryKeys.actionItems,
  queryFn: api.actionItems.open,
  staleTime: 10_000,
});

export const matrixQuery = queryOptions({
  queryKey: queryKeys.matrix,
  queryFn: api.campaigns.matrix,
  staleTime: 5 * 60_000,
});

export const opsHealthQuery = queryOptions({
  queryKey: queryKeys.opsHealth,
  queryFn: api.operations.health,
});

export const opsAuditQuery = queryOptions({
  queryKey: queryKeys.opsAudit,
  queryFn: () => api.operations.audit(50),
});

export function useSession() {
  return useQuery(sessionQuery);
}

export function useMeta() {
  return useQuery(metaQuery);
}

export function setSession(client: QueryClient, session: Session | null): void {
  client.setQueryData(queryKeys.session, session);
}

export function useSwitchWorkspace() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.session.switchWorkspace,
    onSuccess: (session) => setSession(client, session),
  });
}

export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.session.logout,
    onSettled: () => {
      setSession(client, null);
      client.removeQueries({ predicate: (q) => q.queryKey[0] !== "session" });
    },
  });
}
