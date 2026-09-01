import { http, HttpResponse } from "msw";
import type { Meta, Session, Workspace } from "../../src/api/types";

export const WORKSPACES: Workspace[] = [
  { id: "ws_1", slug: "acme", name: "Acme Studio", role: "owner" },
  { id: "ws_2", slug: "side", name: "Side Project", role: "editor" },
];

export function makeSession(overrides: Partial<Session> & { is_owner?: boolean } = {}): Session {
  const { is_owner = true, ...rest } = overrides;
  return {
    account: { id: "acct_1", username: "vega", display_name: "Vega Operator", is_owner },
    workspaces: WORKSPACES,
    current_workspace_id: "ws_1",
    step_up_until: null,
    ...rest,
  };
}

export const META: Meta = { version: "0.1.0-test", environment: "test", distribution_enabled: false, kill_switch: false };

/** Recorded PUT /v1/prefs/theme bodies, reset per test. */
export const themePuts: unknown[] = [];

export const unauthenticated = () => http.get("*/v1/session", () => HttpResponse.json({ detail: "Not signed in" }, { status: 401 }));
export const authenticated = (session: Session = makeSession()) => http.get("*/v1/session", () => HttpResponse.json(session));

export const baseHandlers = [
  unauthenticated(),
  http.get("*/v1/meta", () => HttpResponse.json(META)),
  http.get("*/v1/action-items", () => HttpResponse.json([])),
  http.get("*/v1/runs", () => HttpResponse.json([])),
  http.get("*/v1/workspaces", () => HttpResponse.json(WORKSPACES)),
  http.get("*/v1/prefs/theme", () => HttpResponse.json({ value: null })),
  http.put("*/v1/prefs/theme", async ({ request }) => {
    themePuts.push(await request.json());
    return new HttpResponse(null, { status: 204 });
  }),
  http.delete("*/v1/session", () => new HttpResponse(null, { status: 204 })),
  http.post("*/v1/session/workspace", async ({ request }) => {
    const { workspace_id } = (await request.json()) as { workspace_id: string };
    return HttpResponse.json(makeSession({ current_workspace_id: workspace_id }));
  }),
];
