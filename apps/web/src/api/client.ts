import type { ThemeState } from "@content-factory/web-ui";
import type { LoginResult, Meta, PasskeyOptions, Session, ThemePrefs, Workspace } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
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
    throw new ApiError(res.status, detail);
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
  },
  workspaces: {
    list: () => request<Workspace[]>("GET", "/workspaces").then((r) => r.data),
  },
  prefs: {
    getTheme: () => request<ThemePrefs>("GET", "/prefs/theme").then((r) => r.data),
    putTheme: (value: ThemeState) => request<void>("PUT", "/prefs/theme", { value }).then(() => undefined),
  },
  meta: {
    get: () => request<Meta>("GET", "/meta").then((r) => r.data),
  },
};

export type Api = typeof api;
