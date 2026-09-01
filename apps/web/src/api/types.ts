import type { RunNode } from "@content-factory/pipeline-canvas";
import type { ThemeState } from "@content-factory/web-ui";

export interface Account {
  id: string;
  username: string;
  display_name: string;
  is_owner: boolean;
}

export type WorkspaceRole = "owner" | "editor" | "viewer" | (string & {});

export interface Workspace {
  id: string;
  slug: string;
  name: string;
  role: WorkspaceRole;
}

export interface Session {
  account: Account;
  workspaces: Workspace[];
  current_workspace_id: string | null;
  step_up_until: string | null;
}

export interface MfaRequired {
  mfa_required: true;
  methods: ("totp" | "passkey")[];
}

export type LoginResult = { kind: "session"; session: Session } | { kind: "mfa"; methods: MfaRequired["methods"] };

export interface PasskeyOptions {
  options: unknown;
  challenge_id: string;
}

export interface Meta {
  version: string;
  environment: string;
  distribution_enabled: boolean;
  kill_switch: boolean;
}

export interface ThemePrefs {
  value: ThemeState | null;
}

// --- Pipeline runs ---

export type RunState = "CREATED" | "PREFLIGHTING" | "WAITING_FOR_APPROVAL" | "APPROVED" | "PRODUCING" | "COMPLETE" | "BLOCKED" | "FAILED" | "CANCELLED";

export type RunQuality = "smoke" | "demo";

export interface RunSummary {
  run_id: string;
  state: RunState;
  campaign_id: string;
  project_id: string;
  quality: RunQuality | (string & {});
  created_at: string;
}

export interface RunDetail extends RunSummary {
  preflight_revision_hash: string | null;
  approved_by: string | null;
  error: string | null;
  report: unknown;
  nodes: RunNode[];
}

export type ApprovalDecision = "approve" | "reject";

export interface ApprovalRequest {
  revision_hash: string;
  decision: ApprovalDecision;
  reason?: string;
}

// --- Action Center ---

export type ActionItemSeverity = "info" | "warning" | "critical" | (string & {});

export interface ActionItem {
  id: string;
  kind: string;
  severity: ActionItemSeverity;
  title: string;
  body: string;
  run_id: string | null;
  deep_link: string;
  created_at: string;
}

// --- Revisions ---

export interface FixPlanOutcome {
  kind: "fix_plan";
  plain_language: string;
  estimated_cost_usd: number;
  estimated_seconds: number;
  operations: unknown[];
  impact: { affected_unit_ids: string[] };
}

export interface ClarifyingQuestionOutcome {
  kind: "clarifying_question";
  question: string;
  candidate_unit_ids: string[];
}

export interface RefusalOutcome {
  kind: "refusal";
  policy: string;
  reason: string;
}

export interface GateRequiredOutcome {
  kind: "gate_required";
  gate: string;
  reason: string;
}

export type RevisionOutcome = FixPlanOutcome | ClarifyingQuestionOutcome | RefusalOutcome | GateRequiredOutcome;
