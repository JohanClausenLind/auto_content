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
  personas: ["personas"] as const,
  persona: (id: string) => ["personas", id] as const,
  brandNodes: ["brand-nodes"] as const,
  brandEffective: (id: string) => ["brand-nodes", id, "effective"] as const,
  fanInbox: ["engagement", "inbox"] as const,
  sequences: ["sequences"] as const,
  history: ["run-history"] as const,
  historyRun: (runId: string) => ["run-history", runId] as const,
  runReview: (runId: string) => ["run-history", runId, "review"] as const,
  portalLinks: ["portal-links"] as const,
  portalBriefs: ["portal-briefs"] as const,
  comfyModels: ["comfy-models"] as const,
  comfyDownloads: ["comfy-downloads"] as const,
  modelCatalog: ["model-catalog"] as const,
  hfAccess: ["models", "hf-access"] as const,
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

export const personasQuery = queryOptions({
  queryKey: queryKeys.personas,
  queryFn: () => api.personas.list(),
});

export const personaQuery = (id: string) =>
  queryOptions({
    queryKey: queryKeys.persona(id),
    queryFn: () => api.personas.get(id),
  });

export const brandNodesQuery = queryOptions({
  queryKey: queryKeys.brandNodes,
  queryFn: () => api.brands.nodes(),
});

export const brandEffectiveQuery = (id: string) =>
  queryOptions({
    queryKey: queryKeys.brandEffective(id),
    queryFn: () => api.brands.effective(id),
  });

export const historyQuery = queryOptions({
  queryKey: queryKeys.history,
  queryFn: () => api.history.list(),
  // A run appears here the moment it writes its report, so a modest poll keeps the list honest
  // while several lanes are producing. The rows are cheap: no output scanning.
  refetchInterval: 10_000,
});

export const historyRunQuery = (runId: string) =>
  queryOptions({
    queryKey: queryKeys.historyRun(runId),
    queryFn: () => api.history.get(runId),
    // A finished run's outputs do not change, so this is fetched once and kept.
    staleTime: 5 * 60_000,
  });

/** The frame-review gate of one run: what it is asking, and what has been decided so far.
 *
 * Not polled. Answering it is the only thing that changes it, and the answer comes from this tab;
 * a poll would re-hash every drawing on disk (the digest check behind `on_disk`) every few
 * seconds for a question nobody else is answering.
 */
export const runReviewQuery = (runId: string) =>
  queryOptions({
    queryKey: queryKeys.runReview(runId),
    queryFn: () => api.history.review(runId),
    staleTime: 60_000,
  });

export const sequencesQuery = queryOptions({
  queryKey: queryKeys.sequences,
  queryFn: () => api.sequences.list(),
  refetchInterval: 8000, // sequences appear/grow while a generation run is in progress
});

export const fanInboxQuery = queryOptions({
  queryKey: queryKeys.fanInbox,
  queryFn: () => api.engagement.inbox("pending"),
});

export const portalLinksQuery = queryOptions({
  queryKey: queryKeys.portalLinks,
  queryFn: () => api.portal.links(),
});

export const portalBriefsQuery = queryOptions({
  queryKey: queryKeys.portalBriefs,
  queryFn: () => api.portal.briefs(),
});

export const comfyModelsQuery = queryOptions({
  queryKey: queryKeys.comfyModels,
  queryFn: () => api.comfy.models(),
  staleTime: 30_000,
});

export const comfyDownloadsQuery = queryOptions({
  queryKey: queryKeys.comfyDownloads,
  queryFn: () => api.comfy.downloads(),
  // While a transfer runs, keep polling; refreshing also re-checks comfy-cli's status.
  refetchInterval: (query) => (query.state.data?.some((j) => j.state === "running") ? 2500 : false),
});

/** The weight store: what is installed, what is missing, and what is downloading right now. */
export const modelCatalogQuery = queryOptions({
  queryKey: queryKeys.modelCatalog,
  queryFn: () => api.models.catalog(),
  // One request answers the whole page, so it can poll while a transfer is in flight and sit
  // still otherwise. Scanning the store is a stat walk, not a network call.
  refetchInterval: (query) =>
    query.state.data?.jobs.some((job) => job.state === "running") ? 2000 : false,
});

export function useInstallModel() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => api.models.install(key),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.modelCatalog });
      void client.invalidateQueries({ queryKey: queryKeys.comfyModels });
    },
  });
}

export const hfAccessQuery = queryOptions({
  queryKey: queryKeys.hfAccess,
  queryFn: () => api.models.hfAccess(),
});

export function useStoreHfToken() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (token: string) => api.models.putHfAccess(token),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.hfAccess }),
  });
}

export function useForgetHfToken() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.models.forgetHfAccess(),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.hfAccess }),
  });
}

export function useRelinkModels() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.models.relink(),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.modelCatalog });
      void client.invalidateQueries({ queryKey: queryKeys.comfyModels });
    },
  });
}

export function useStartModelDownload() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: import("./types").ModelDownloadBody) => api.comfy.download(body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.comfyDownloads });
      void client.invalidateQueries({ queryKey: queryKeys.comfyModels });
    },
  });
}

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
