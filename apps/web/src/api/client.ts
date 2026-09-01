import type { ThemeState } from "@content-factory/web-ui";
import type {
  ActionItem,
  ApprovalRequest,
  AuditEvent,
  CampaignBody,
  CampaignCreated,
  BrandEffective,
  BrandNode,
  EngagementSyncResult,
  FanMessage,
  CampaignPreview,
  DeliverableMatrix,
  LoginResult,
  Meta,
  OperationsHealth,
  PasskeyOptions,
  PersonaDetail,
  PersonaDiff,
  PersonaSummary,
  PortalBriefRow,
  SequenceSummary,
  PortalLinkCreated,
  PortalLinkRow,
  PushSubscriptionBody,
  PushTestResult,
  RevisionOutcome,
  RunDetail,
  RunQuality,
  RunSummary,
  Session,
  ThemePrefs,
  Workspace,
} from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  /** True when the server answered 403 with `X-Step-Up: required` (re-auth needed). */
  readonly stepUpRequired: boolean;
  constructor(status: number, detail: string, stepUpRequired = false) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.stepUpRequired = stepUpRequired;
  }
}

export const isApiError = (e: unknown): e is ApiError => e instanceof ApiError;

const BASE = "/v1";

function url(path: string): string {
  const origin = typeof location !== "undefined" ? location.href : "http://localhost/";
  return new URL(`${BASE}${path}`, origin).toString();
}

async function request<T>(method: string, path: string, body?: unknown): Promise<{ status: number; data: T }> {
  let res: Response;
  try {
    res = await fetch(url(path), {
      method,
      credentials: "include",
      headers: { Accept: "application/json", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check that the API is running.");
  }
  if (res.status === 204) return { status: 204, data: undefined as T };
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data && typeof (data as { detail: unknown }).detail === "string"
        ? (data as { detail: string }).detail
        : res.status === 401
          ? "You're signed out."
          : `Request failed (${res.status}).`;
    throw new ApiError(res.status, detail, res.headers.get("X-Step-Up") === "required");
  }
  return { status: res.status, data: data as T };
}

export const api = {
  session: {
    get: () => request<Session>("GET", "/session").then((r) => r.data),
    login: async (username: string, password: string): Promise<LoginResult> => {
      const r = await request<Session | { mfa_required: true; methods: ("totp" | "passkey")[] }>("POST", "/session", { username, password });
      if (r.status === 202 && "mfa_required" in r.data) return { kind: "mfa", methods: r.data.methods };
      return { kind: "session", session: r.data as Session };
    },
    totp: (code: string) => request<Session>("POST", "/session/totp", { code }).then((r) => r.data),
    logout: () => request<void>("DELETE", "/session").then(() => undefined),
    passkeyOptions: () => request<PasskeyOptions>("POST", "/session/passkey/options").then((r) => r.data),
    passkeyVerify: (challenge_id: string, credential: unknown) => request<Session>("POST", "/session/passkey/verify", { challenge_id, credential }).then((r) => r.data),
    switchWorkspace: (workspace_id: string) => request<Session>("POST", "/session/workspace", { workspace_id }).then((r) => r.data),
    stepUp: (password: string) => request<unknown>("POST", "/session/step-up", { password }).then(() => undefined),
  },
  runs: {
    list: () => request<RunSummary[]>("GET", "/runs").then((r) => r.data),
    get: (runId: string) => request<RunDetail>("GET", `/runs/${encodeURIComponent(runId)}`).then((r) => r.data),
    start: (quality: RunQuality) => request<{ run_id: string }>("POST", "/runs", { quality, campaign: "fixture" }).then((r) => r.data),
    approval: (runId: string, body: ApprovalRequest) => request<unknown>("POST", `/runs/${encodeURIComponent(runId)}/approval`, body).then(() => undefined),
  },
  actionItems: {
    open: () => request<ActionItem[]>("GET", "/action-items?status_filter=open").then((r) => r.data),
  },
  revisions: {
    propose: (body: { project_id: string; unit_id?: string; feedback: string }) => request<{ outcome: RevisionOutcome }>("POST", "/revisions", body).then((r) => r.data.outcome),
    apply: (body: { project_id: string; feedback: string }) => request<{ run_id: string }>("POST", "/revisions/apply", body).then((r) => r.data),
  },
  campaigns: {
    matrix: () => request<DeliverableMatrix>("GET", "/campaigns/matrix").then((r) => r.data),
    preview: (body: CampaignBody) => request<CampaignPreview>("POST", "/campaigns/preview", body).then((r) => r.data),
    create: (body: CampaignBody) => request<CampaignCreated>("POST", "/campaigns", body).then((r) => r.data),
  },
  operations: {
    health: () => request<OperationsHealth>("GET", "/operations/health").then((r) => r.data),
    audit: (limit = 50) => request<AuditEvent[]>("GET", `/operations/audit?limit=${limit}`).then((r) => r.data),
  },
  notifications: {
    vapidPublicKey: () => request<{ public_key: string }>("GET", "/notifications/vapid-public-key").then((r) => r.data),
    subscribe: (body: PushSubscriptionBody) => request<{ id: string }>("POST", "/notifications/subscriptions", body).then((r) => r.data),
    unsubscribe: (endpoint: string) => request<void>("DELETE", `/notifications/subscriptions?endpoint=${encodeURIComponent(endpoint)}`).then(() => undefined),
    test: () => request<PushTestResult>("POST", "/notifications/test").then((r) => r.data),
  },
  workspaces: {
    list: () => request<Workspace[]>("GET", "/workspaces").then((r) => r.data),
  },
  prefs: {
    getTheme: () => request<ThemePrefs>("GET", "/prefs/theme").then((r) => r.data),
    putTheme: (value: ThemeState) => request<void>("PUT", "/prefs/theme", { value }).then(() => undefined),
    get: (key: string) => request<{ value: unknown }>("GET", `/prefs/${encodeURIComponent(key)}`).then((r) => r.data),
    put: (key: string, value: unknown) => request<void>("PUT", `/prefs/${encodeURIComponent(key)}`, { value }).then(() => undefined),
  },
  meta: {
    get: () => request<Meta>("GET", "/meta").then((r) => r.data),
  },
  personas: {
    list: () => request<PersonaSummary[]>("GET", "/personas").then((r) => r.data),
    get: (id: string) => request<PersonaDetail>("GET", `/personas/${encodeURIComponent(id)}`).then((r) => r.data),
    create: (doc: unknown) => request<PersonaDetail>("POST", "/personas", doc).then((r) => r.data),
    revise: (id: string, feedback: string) => request<PersonaDiff>("POST", `/personas/${encodeURIComponent(id)}/revise`, { feedback }).then((r) => r.data),
    apply: (id: string, diff: PersonaDiff) => request<PersonaDetail>("POST", `/personas/${encodeURIComponent(id)}/apply`, diff).then((r) => r.data),
  },
  brands: {
    nodes: () => request<BrandNode[]>("GET", "/brand-nodes").then((r) => r.data),
    create: (body: { name: string; parent_id?: string | null; tokens?: Record<string, string>; locked_tokens?: string[]; policies?: Record<string, string>; locked_policies?: string[] }) =>
      request<BrandNode>("POST", "/brand-nodes", body).then((r) => r.data),
    effective: (id: string) => request<BrandEffective>("GET", `/brand-nodes/${encodeURIComponent(id)}/effective`).then((r) => r.data),
  },
  sequences: {
    list: () => request<SequenceSummary[]>("GET", "/sequences").then((r) => r.data),
    fileUrl: (name: string, file: string) => url(`/sequences/${encodeURIComponent(name)}/files/${file}`),
  },
  engagement: {
    inbox: (disposition = "pending") => request<FanMessage[]>("GET", `/engagement/inbox?disposition=${disposition}`).then((r) => r.data),
    setDisposition: (id: string, disposition: "answered" | "skipped", reason?: string) =>
      request<FanMessage>("POST", `/engagement/inbox/${encodeURIComponent(id)}/disposition`, { disposition, ...(reason ? { reason } : {}) }).then((r) => r.data),
    sync: () => request<EngagementSyncResult>("POST", "/engagement/sync").then((r) => r.data),
  },
  portal: {
    links: () => request<PortalLinkRow[]>("GET", "/portal-links").then((r) => r.data),
    createLink: (label: string) => request<PortalLinkCreated>("POST", "/portal-links", { label }).then((r) => r.data),
    revokeLink: (id: string) => request<void>("DELETE", `/portal-links/${encodeURIComponent(id)}`).then(() => undefined),
    briefs: () => request<PortalBriefRow[]>("GET", "/portal-briefs").then((r) => r.data),
    decide: (id: string, decision: "accepted" | "declined") => request<PortalBriefRow>("POST", `/portal-briefs/${encodeURIComponent(id)}/decision`, { decision }).then((r) => r.data),
  },
};

export type Api = typeof api;
