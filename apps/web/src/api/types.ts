import type { RunNode } from "@content-factory/pipeline-canvas";

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
  /** Free-form JSON from /v1/prefs/theme: validated by the ThemeProvider, never trusted here. */
  value: unknown;
}

// --- Pipeline runs ---

export type RunState = "CREATED" | "PREFLIGHTING" | "WAITING_FOR_APPROVAL" | "APPROVED" | "PRODUCING" | "COMPLETE" | "BLOCKED" | "FAILED" | "CANCELLED";

export type RunQuality = "smoke" | "demo";

export interface ComfyModelRoot {
  path: string;
  source: "settings" | "comfy-cli" | "extra" | (string & {});
  exists: boolean;
}

export interface ComfyModelFile {
  kind: string;
  filename: string;
  relative_path: string;
  size_bytes: number;
  root: string;
}

export interface ComfyModelsInventory {
  roots: ComfyModelRoot[];
  models: ComfyModelFile[];
}

/** One file a weight family puts in the store, and whether ComfyUI can see it. */
export interface ModelFileStatus {
  store_rel: string;
  filename: string;
  state: "present" | "missing" | "partial";
  size_bytes: number;
  comfy_folder: string;
  comfy_linked: boolean;
}

export interface ModelSource {
  repo_id?: string;
  url?: string;
  revision: string;
  gated: "no" | "auto" | "manual";
  note: string;
}

/** A weight family as the store page sees it: what it is for, what it costs, what is on disk. */
export interface ModelPackage {
  key: string;
  name: string;
  purpose: string;
  license: string;
  approx_bytes: number;
  installable: boolean;
  manual: string;
  gating: "no" | "auto" | "manual";
  caveat: string;
  sources: ModelSource[];
  build_command: string[];
  state: "ready" | "partial" | "absent" | "manual";
  files: ModelFileStatus[];
  bytes_on_disk: number;
  store_path: string;
  index_path: string;
  index_state: "ok" | "missing" | "broken" | "not_indexed";
  wanted_by: string[];
  required: boolean;
  labels: string[];
}

export interface SkillEnvEntry {
  key: string;
  skill: string;
  name: string;
  purpose: string;
  caveat: string;
  state: "ready" | "absent";
  path: string;
  wanted_by: string[];
  required: boolean;
  labels: string[];
}

export interface InstallStep {
  label: string;
  state: "pending" | "running" | "done" | "failed" | "skipped";
  detail: string;
}

export interface InstallJob {
  key: string;
  kind: "weights" | "skill_env";
  name: string;
  state: "running" | "complete" | "failed" | "already_installed" | "needs_access";
  detail: string;
  steps: InstallStep[];
  bytes_expected: number;
  bytes_done: number;
  started_at: number;
  finished_at: number | null;
}

export interface ModelCatalog {
  store: string;
  store_exists: boolean;
  store_free_bytes: number;
  store_total_bytes: number;
  index_root: string;
  comfy_models_dir: string;
  packages: ModelPackage[];
  skill_envs: SkillEnvEntry[];
  jobs: InstallJob[];
}

export interface HuggingFaceAccess {
  present: boolean;
  handle: string;
  stored_at: string | null;
}

export interface RelinkReport {
  index: string[];
  comfy: string[];
  skipped: string[];
}

/** What the server made of a file dropped on the canvas. */
export interface DropSuggestion {
  node_type: string;
  title: string;
  why: string;
  to_slot: string;
  values: Record<string, string | number | boolean>;
}

/** What was done to a dropped file to make it readable, when anything was. */
export interface DropConversion {
  action: "remux" | "transcode" | "rewrite" | "none";
  from_mime: string;
  to_mime: string;
  detail: string;
}

export interface UploadedDrop {
  asset_id: string;
  filename: string;
  /** What the file is. Not always what `mime` says: an MP4 with nothing to look at is audio. */
  kind: "image" | "video" | "audio" | "document" | "data" | "text";
  mime: string;
  size_bytes: number;
  /** What arrived, before conversion; differs from size_bytes when it was re-encoded. */
  uploaded_bytes: number;
  conversion: DropConversion | null;
  /** Why a file whose MIME says video is being offered as a recording; null when it is a film. */
  blank_picture: string | null;
  sha256: string;
  facts: Record<string, string | number>;
  /** One line describing what actually arrived, measured rather than guessed. */
  description: string;
  node_type: string;
  node_slot: string;
  suggestions: DropSuggestion[];
}

export interface GraphSummary {
  graph_id: string;
  name: string;
  nodes: number;
  links: number;
  updated_at: string;
}

export interface GraphDisposition {
  node_id: string;
  type: string;
  kind: "executes" | "skipped" | "blocks" | (string & {});
  reason: string;
  dag_node_id: string | null;
}

export interface GraphCompileResult {
  ok: boolean;
  problems: string[];
  deliverable_type: string | null;
  dispositions: GraphDisposition[];
  dag_nodes: number;
}

export interface GraphRunStarted extends GraphCompileResult {
  run_id: string;
}

export type DownloadJobState = "running" | "complete" | "failed" | "already_installed" | (string & {});

export interface ModelDownloadBody {
  url: string;
  relative_path: string;
  filename: string;
}

export interface DownloadJob {
  job_id: string;
  url: string;
  relative_path: string;
  filename: string;
  state: DownloadJobState;
  detail: string;
  started_at: number;
}

export interface RunSummary {
  run_id: string;
  state: RunState;
  campaign_id: string;
  project_id: string;
  quality: RunQuality | (string & {});
  created_at: string;
}

/**
 * When the run will be done, estimated from the durations this machine has already measured for
 * these stages (`services/durations.py`). Null once nothing is left to wait for.
 */
export interface RunEta {
  /** Seconds of work left: the queued stages plus what remains of the one in flight. */
  remaining_seconds: number;
  /** ISO-8601, UTC and aware, so the browser can render it in local time. */
  finish_at: string;
  /** Past runs behind the weakest term in the total. A total is only as good as its worst term. */
  samples: number;
  /** Three or more samples for every stage, and no stage missing from the history. */
  confident: boolean;
  /** The running stage has already outlasted its median — the honest reading of "0 left". */
  overdue: boolean;
  /** Stages with no timing history, named rather than silently counted as free. */
  unknown_stages: string[];
}

export interface RunDetail extends RunSummary {
  preflight_revision_hash: string | null;
  approved_by: string | null;
  error: string | null;
  report: unknown;
  /** Real dependency edges (compiled workspace graphs); null for legacy/campaign chains. */
  edges: { source: string; target: string }[] | null;
  /** Estimated finish; null when the run has no unfinished nodes left. */
  eta: RunEta | null;
  nodes: RunNode[];
}

// --- Run history (local runs on this machine) ---
//
// The counterpart to RunSummary: those are durable Temporal runs from the database, these are the
// runs made with `content-factory make`, which write no database row and whose outputs — every
// film, drawing and narration on this machine — were unreachable from the app before this.

/** What a run is, or the state it ended in. `running` comes from the run registry rather than
 *  the report: a report only carries `passed` at the very end, so mid-flight a healthy run is
 *  indistinguishable from one whose last stage gave up — and read as `failed`. */
export type RunOutcome = "running" | "complete" | "review" | "blocked" | "stopped" | "failed";

/** What kind of thing a file is, which decides whether it gets a player, a grid or a link. */
export type OutputKind = "image" | "video" | "audio" | "text" | "data";

export type Attribution = "recorded" | "inferred";

export interface RunOutput {
  /** Relative to the run directory. Both the id and what you pass to `fileUrl`. */
  path: string;
  kind: OutputKind;
  /** Where it came from: video, anchor, frame, control, audio, caption, metadata… */
  role: string;
  bytes: number;
  content_type: string;
  /** The lane's key for the step that made it — `anchor`, `spokes` — or null when neither the
   *  run's own record nor the path table could place it. */
  node: string | null;
  stage: string | null;
  /** `recorded` when the run observed which step wrote this, `inferred` when the path said so. */
  attribution: Attribution | null;
}

/**
 * One step of a local run: what it did, and what it produced.
 *
 * Named `RunStep`, not `RunNode`, because `RunNode` is already the durable pipeline's DAG node
 * (imported from `pipeline-canvas` above) and the two are not the same thing: that one is a
 * database row in a Temporal run, this one is a step in a `run.json` on disk. The two run worlds
 * have no join, and giving them one name is how somebody would come to believe they do.
 */
export interface RunStep {
  /** The lane's own key, which is what `GraphNode.key` carries — this is the join. */
  node: string;
  stage: string;
  ok: boolean;
  /** Frozen by a pin; its files are the ones the run that made them left. */
  pinned: boolean;
  /** Stopped for a person, not because it broke. */
  blocked: boolean;
  /** Whether this run has a record at this node at all. False for the nodes before a `--from`
   *  resume point: not a failure, a step that did not happen this time. */
  ran: boolean;
  seconds: number;
  error: string | null;
  /** What the stage said about its own work — attempts, backend, drift numbers. */
  facts: Record<string, unknown>;
  /** Paths, relative to the run directory. */
  outputs: string[];
  outputs_total: number;
  attribution: Attribution;
}

export interface HistoryRun {
  /** The run's directory under `output/`, with "/" written "~" — e.g. `overnight~ps1c-pinecone`. */
  run_id: string;
  /** What the run was rendering, in the operator's own words, or null.
   *
   *  On the cheap list as well as the detail, because without it the history is 241 directory
   *  names: `a20-imageset-owl` says which lane ran and nothing about what came out of it. */
  subject: string | null;
  workflow: string | null;
  outcome: RunOutcome;
  /** Epoch seconds: when the run last wrote anything. */
  finished_at: number;
  /** What it actually cost. This is the evidence behind every ETA. */
  seconds: number;
  stages: number;
  stages_ok: number;
  blocked_at: string | null;
  project_dir: string;
  deliverable_id: string | null;
  outputs_total: number;
  /** Drawings this run is waiting on somebody to look at. Not the same as `outcome === "review"`:
   *  a run whose frames were all rejected is parked at the gate too, and it needs a redraw. */
  awaiting_review: number;
}

export interface HistoryRunDetail extends HistoryRun {
  outputs: RunOutput[];
  /** The lane's steps in order, each with the files it produced. */
  nodes: RunStep[];
  /** The finished film, when the run made one. */
  film: string | null;
  /** One image to represent the run — never a control map. */
  poster: string | null;
  /** Files no step claimed. Reported rather than hidden: on a run that predates per-node
   *  recording it is the difference between "this node made nothing" and "nobody wrote down
   *  which node made this". Markers are not counted. */
  unattributed: number;
}

// --- The frame-review gate ---

/** One measured property of one drawing. Advisory findings inform the reviewer; a blocker fails
 *  the frame on its own, whatever anybody says about it. */
export interface ReviewFinding {
  check: string;
  passed: boolean;
  severity: "blocker" | "advisory" | (string & {});
  detail: string;
  measured: number | null;
  threshold: number | null;
}

export type FrameVerdict = "accept" | "reject" | "unreviewed";

export interface ReviewFrame {
  frame_id: string;
  verdict: FrameVerdict;
  /** What is wrong with the picture. The record, and what the guidance proposals are built from. */
  reason: string;
  /** What it should show instead, stated positively. This is the part appended to the prompt when
   *  the frame is redrawn — see FrameRecord.redirect. */
  redirect: string;
  /** Relative to the run directory — fetched through the same files route as any other output. */
  image: string | null;
  png_sha256: string;
  /** Whether the file still hashes to what the batch decided on. False means the picture was
   *  regenerated after the gate ran, so this is not what a verdict would bind to. */
  on_disk: boolean;
  blocked: boolean;
  findings: ReviewFinding[];
}

export type ReviewerKind = "operator" | "agent" | "vlm";

/** One deliverable's frame-review gate. */
export interface RunReview {
  deliverable: string;
  deliverable_id: string;
  contact_sheet: string | null;
  reviewer: ReviewerKind | null;
  reviewed_at: string | null;
  notes: string;
  passed: boolean;
  frames: ReviewFrame[];
  unreviewed: number;
  rejected: number;
  accepted: number;
  flagged: number;
}

export interface RunReviewPage {
  run_id: string;
  workflow: string | null;
  project_dir: string;
  /** A verdict unblocks the gate; it does not restart the stages after it. This is what does. */
  resume_command: string;
  reviews: RunReview[];
  /** The vision model's stored opinion per deliverable, when one has been asked for. Served with
   *  the gate so the panel knows whether one exists before offering to spend the GPU. */
  ai_reviews: Record<string, AiSetReview>;
}

// --- The vision model's second opinion ---
//
// An opinion and never a verdict. It answers what the measurements cannot — whether the subject
// is the same subject — and the operator is still the only reviewer that can accept or reject a
// frame. `shows` is first in the UI for the same reason it is first in the prompt: it is how a
// reader tells whether the model looked at the picture or at the brief.

export type OpinionSeverity = "fine" | "minor" | "wrong";

export interface FrameOpinion {
  frame_id: string;
  /** What the model says is in the picture. The check on the reviewer, read against the image. */
  shows: string;
  matches_intent: boolean;
  issues: string[];
  severity: OpinionSeverity;
}

export interface SetOpinion {
  /** Whether the frames read as one subject, one place and one idiom. */
  same_world: boolean;
  /** What actually differs across the set, named concretely. */
  what_changes: string[];
  /** The frames that left the others behind. Empty when there is no odd one out. */
  drifting_frames: string[];
  summary: string;
}

export interface AiSetReview {
  deliverable_id: string;
  reviewed_at: string;
  model_alias: string;
  model_id: string;
  /** The story and per-frame context the model was given, verbatim. A judgement is worth what
   *  the judge was told, and this is the only way to tell a wrong picture from a missing brief. */
  intent: string;
  frames: FrameOpinion[];
  set: SetOpinion;
  digests: Record<string, string>;
  elapsed_s: number;
  input_tokens: number;
  output_tokens: number;
  /** Whether it is about the pictures now on disk. False means the frames were redrawn since. */
  current: boolean;
  /** The frames the model would not pass, offered as what to look at first — never applied. */
  flagged: string[];
}

export interface VerdictBody {
  deliverable?: string;
  accept?: string[];
  reject?: string[];
  /** Accept every frame not named as rejected — a person's yes to one contact sheet. */
  accept_rest?: boolean;
  /** Why the rejected frames are wrong. */
  reason?: string;
  /** What they should show instead, positively — the part that reaches the model on the redraw. */
  redirect?: string;
  note?: string;
  reviewer?: ReviewerKind;
}

/** Why a verdict was refused, from the 422 body: which rule, and which frames it is about. */
export interface VerdictRefusal {
  problem: string;
  kind: string;
  frames: string[];
  details: string[];
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

// --- Campaigns (Create flow) ---

export interface MatrixRow {
  type: string;
  supported: boolean;
  /** Plain-language: why it works (supported) or why it doesn't (unsupported). */
  reason: string;
  destinations: string[];
}

export interface DeliverableMatrix {
  rows: MatrixRow[];
  notes: string[];
}

export interface DeliverableChoice {
  type: string;
  title: string;
  card_count?: number;
}

export interface CampaignBody {
  topic: string;
  objective: string;
  deliverables: DeliverableChoice[];
  quality: RunQuality;
}

export interface DagNode {
  node_id: string;
  stage: string;
  deliverable_id: string | null;
  depends_on: string[];
}

export interface PrunedStage {
  stage: string;
  deliverable_id: string | null;
  reason: string;
}

export interface CampaignEstimates {
  external_cost_usd: number;
  external_calls: number;
  local_render: boolean;
}

/** The slice of the compiled campaign the preview screen needs; the server sends more. */
export interface PreviewDeliverable {
  deliverable_id: string;
  type: string;
  title: string;
}

export interface CampaignPreview {
  campaign: { campaign_id: string; deliverables: PreviewDeliverable[] };
  dag: { nodes: DagNode[]; pruned: PrunedStage[] };
  estimates: CampaignEstimates;
  notes: string[];
}

export interface CampaignCreated {
  run_id: string;
  campaign_id: string;
}

// --- Operations ---

export type HealthStatus = "ok" | "warn" | "fail" | "skip" | (string & {});

export interface HealthCheck {
  name: string;
  status: HealthStatus;
  detail: string;
  fix: string | null;
}

export interface OperationsHealth {
  ok: boolean;
  checks: HealthCheck[];
}

export interface AuditEvent {
  id: string;
  action: string;
  actor: string | null;
  workspace_id: string | null;
  target: string | null;
  created_at: string;
}

// --- Notifications (web push) ---

export interface PushSubscriptionBody {
  endpoint: string;
  keys: { p256dh: string; auth: string };
  user_agent?: string;
}

export interface PushTestResult {
  sent: number;
  total: number;
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

// --- Personas ---

export interface PersonaSummary {
  id: string;
  name: string;
  revision: number;
  archived: boolean;
}

export interface PersonaVoiceDoc {
  tone: string[];
  vocabulary: string;
  sentence_length: string;
  emoji_policy: string;
  punctuation: string;
  humor: string;
  pet_names: string[];
}

export interface PersonaDocument {
  persona_id: string;
  revision: number;
  identity: { display_name: string; pronouns: string; presented_age: number };
  voice: PersonaVoiceDoc;
  backstory: { bio: string };
  disclosure: "disclose_on_ask" | "deflect";
}

export interface PersonaDetail extends PersonaSummary {
  document: PersonaDocument;
}

export interface PersonaFieldChange {
  path: string;
  before: string;
  after: string;
  reason: string;
}

export interface PersonaDiff {
  persona_id: string;
  base_revision: number;
  changes: PersonaFieldChange[];
  plain_language: string;
}

// --- Brand hierarchy ---

export interface BrandNode {
  id: string;
  parent_id: string | null;
  name: string;
  tokens: Record<string, string>;
  locked_tokens: string[];
  policies: Record<string, string>;
  locked_policies: string[];
}

export interface BrandEffective {
  tokens: Record<string, string>;
  policies: Record<string, string>;
}

// --- Portal ---

export interface PortalLinkRow {
  id: string;
  label: string;
  expires_at: string;
  revoked: boolean;
}

export interface PortalLinkCreated extends PortalLinkRow {
  token: string;
  submit_url: string;
}

export interface PortalBriefRow {
  id: string;
  topic: string;
  objective: string;
  deadline: string | null;
  contact: string;
  status: "new" | "accepted" | "declined";
  created_at: string | null;
}

// --- Engagement inbox ---

export interface FanMessage {
  id: string;
  platform: string;
  account: string;
  fan_id: string;
  text: string;
  received_at: string;
  message_class: string;
  vip: boolean;
  disposition: "pending" | "answered" | "skipped";
  skip_reason: string | null;
}

export interface EngagementSyncResult {
  fetched: number;
  stored: number;
  escalated: number;
  accounts: number;
}

// --- Image sequences ---

export interface SequenceFrame {
  index: number;
  file: string;
  attempts: number | null;
  cache_hit: boolean;
  drift: { locked: number; style: number } | null;
}

export interface SequenceSummary {
  name: string;
  anchor: boolean;
  frames: SequenceFrame[];
  videos: string[];
  contact_sheet: boolean;
  flipbook: boolean;
  updated_at: number | null;
}
