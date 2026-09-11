import { http, HttpResponse } from "msw";
import type { Meta, Session, Workspace } from "../../src/api/types";
import { productHandlers } from "./campaigns";
import { AI_REVIEW, HISTORY_DETAIL, HISTORY_RUNS, REVIEW_PAGE } from "./runs";

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

/** Local model inventory: the LTX-2.5 stack present, everything else missing. */
export const COMFY_INVENTORY = {
  roots: [{ path: "/fake/ComfyUI/models", source: "comfy-cli", exists: true }],
  models: [
    { kind: "diffusion_models", filename: "ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf", relative_path: "diffusion_models/ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf", size_bytes: 1, root: "/fake/ComfyUI/models" },
    { kind: "text_encoders", filename: "gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf", relative_path: "text_encoders/gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf", size_bytes: 1, root: "/fake/ComfyUI/models" },
    { kind: "vae", filename: "ltx-2.5-video-vae-conv-bf16.safetensors", relative_path: "vae/ltx-2.5-video-vae-conv-bf16.safetensors", size_bytes: 1, root: "/fake/ComfyUI/models" },
    { kind: "vae", filename: "ltx-2.5-audio-vae-bf16(1).safetensors", relative_path: "vae/ltx-2.5-audio-vae-bf16(1).safetensors", size_bytes: 1, root: "/fake/ComfyUI/models" },
  ],
};


/**
 * The model store as the tests see it: one family installed, one missing and required, one
 * part-downloaded with a broken index link, one behind a manual access gate, and one skill env
 * that has not been built. Every state the page has to render is in here.
 */
export const MODEL_CATALOG = {
  store: "/mnt/fast/models",
  store_exists: true,
  store_free_bytes: 620_000_000_000,
  store_total_bytes: 937_000_000_000,
  index_root: "/repo/models",
  comfy_models_dir: "/fake/ComfyUI/models",
  packages: [
    {
      key: "ltx-2.5",
      name: "LTX-2.5 22B distilled (Q5_K_M GGUF)",
      purpose: "Turns a drawn keyframe into motion",
      license: "other (Lightricks LTX licence)",
      approx_bytes: 29_200_000_000,
      installable: true,
      manual: "",
      gating: "auto",
      caveat: "",
      sources: [{ repo_id: "elix3r/LTX-2.5-22b-distilled-GGUF", revision: "1cd163da90282be382ae5afcb93348a1eed46a88", gated: "auto", note: "" }],
      build_command: [],
      state: "ready",
      files: [
        { store_rel: "diffusion_models/ltx.gguf", filename: "ltx.gguf", state: "present", size_bytes: 16_878_535_040, comfy_folder: "models/diffusion_models", comfy_linked: true },
      ],
      bytes_on_disk: 29_200_000_000,
      store_path: "/mnt/fast/models/ltx25",
      index_path: "/repo/models/video_generation/LTX-2.5",
      index_state: "ok",
      wanted_by: ["image-to-video", "narrated-video"],
      required: true,
      labels: ["LTX-2.5 transformer"],
    },
    {
      key: "gimm-vfi",
      name: "GIMM-VFI (quality frame interpolation)",
      purpose: "Fills frames between drawings",
      license: "unstated on the Hub repo",
      approx_bytes: 290_000_000,
      installable: true,
      manual: "",
      gating: "no",
      caveat: "",
      sources: [{ repo_id: "GSean/GIMM-VFI", revision: "ab7735cdcfbd2e03c1bf2819380a25e8a4f321d1", gated: "no", note: "" }],
      build_command: [],
      state: "absent",
      files: [
        { store_rel: "gimmvfi_r_arb.pt", filename: "gimmvfi_r_arb.pt", state: "missing", size_bytes: 0, comfy_folder: "", comfy_linked: false },
      ],
      bytes_on_disk: 0,
      store_path: "/mnt/fast/models/gimm-vfi",
      index_path: "/repo/models/frame_interpolation/GIMM-VFI",
      index_state: "missing",
      wanted_by: ["image-to-video"],
      required: true,
      labels: ["GIMM-VFI interpolation weights"],
    },
    {
      key: "krea2",
      name: "Krea 2 Turbo (fp8)",
      purpose: "Styled keyframes through ComfyUI",
      license: "other (Krea 2 licence)",
      approx_bytes: 18_600_000_000,
      installable: true,
      manual: "",
      gating: "no",
      caveat: "cannot take control passes as references",
      sources: [{ repo_id: "Comfy-Org/Krea-2", revision: "e5ea8b4dd7f38f348b138eb0fe29f92c0e367e96", gated: "no", note: "" }],
      build_command: [],
      state: "partial",
      files: [
        { store_rel: "diffusion_models/krea2_turbo_fp8_scaled.safetensors", filename: "krea2_turbo_fp8_scaled.safetensors", state: "partial", size_bytes: 1_000, comfy_folder: "models/diffusion_models", comfy_linked: false },
      ],
      bytes_on_disk: 1_000,
      store_path: "/mnt/fast/models/krea2",
      index_path: "/repo/models/image_generation/Krea2",
      index_state: "broken",
      wanted_by: ["single-image"],
      required: false,
      labels: ["Krea2 transformer"],
    },
    {
      key: "sam-3.1",
      name: "SAM 3.1 (segmentation/tracking)",
      purpose: "The quality tracker for fix_video",
      license: "other (Meta SAM licence)",
      approx_bytes: 3_500_000_000,
      installable: true,
      manual: "",
      gating: "manual",
      caveat: "",
      sources: [{ repo_id: "facebook/sam3.1", revision: "daa63191845a41281374e725f4c9e51c7a824460", gated: "manual", note: "request access on the model page" }],
      build_command: [],
      state: "absent",
      files: [
        { store_rel: "sam3.1_multiplex.pt", filename: "sam3.1_multiplex.pt", state: "missing", size_bytes: 0, comfy_folder: "", comfy_linked: false },
      ],
      bytes_on_disk: 0,
      store_path: "/mnt/fast/models/sam3.1",
      index_path: "/repo/models/video_editing/SAM-3.1",
      index_state: "missing",
      wanted_by: [],
      required: false,
      labels: [],
    },
    {
      key: "rife",
      name: "Practical-RIFE 4.25",
      purpose: "The fast interpolation tier",
      license: "MIT (code)",
      approx_bytes: 0,
      installable: false,
      manual: "upstream publishes the weights on Google Drive; fetch train_log/ by hand",
      gating: "no",
      caveat: "",
      sources: [],
      build_command: [],
      state: "manual",
      files: [
        { store_rel: "train_log/flownet.pkl", filename: "flownet.pkl", state: "missing", size_bytes: 0, comfy_folder: "", comfy_linked: false },
      ],
      bytes_on_disk: 0,
      store_path: "/mnt/fast/models/rife",
      index_path: "/repo/models/frame_interpolation/Practical-RIFE",
      index_state: "missing",
      wanted_by: ["photo-sequence-video"],
      required: true,
      labels: ["Practical-RIFE weights"],
    },
  ],
  skill_envs: [
    {
      key: "skill:skills/audio/kokoro",
      skill: "skills/audio/kokoro",
      name: "Kokoro voice",
      purpose: "The lighter alternative narration voice",
      caveat: "",
      state: "absent",
      path: "/repo/skills/audio/kokoro",
      wanted_by: ["narrated-video"],
      required: false,
      labels: ["Kokoro skill env"],
    },
    {
      key: "skill:skills/image/hidream",
      skill: "skills/image/hidream",
      name: "HiDream image server",
      purpose: "Runs the local HiDream-O1 server",
      caveat: "",
      state: "ready",
      path: "/repo/skills/image/hidream",
      wanted_by: ["single-image"],
      required: true,
      labels: ["HiDream skill env"],
    },
  ],
  jobs: [] as unknown[],
};

/** Recorded POST /v1/models/install keys, reset per test. */
export const installPosts: string[] = [];
export const relinkPosts: number[] = [];

/** What POST /v1/uploads answers for a dropped 16 kHz mono take. */
export const DROPPED_AUDIO = {
  asset_id: "ws_workspace01/originals-audio/ab/" + "a".repeat(64) + ".wav",
  filename: "take one.wav",
  kind: "audio" as const,
  mime: "audio/x-wav",
  size_bytes: 64000,
  sha256: "a".repeat(64),
  uploaded_bytes: 64000,
  conversion: null,
  facts: { duration_ms: 2000, sample_rate_hz: 16000, channels: 1 },
  description: "audio · 2.0 s · 16 kHz · mono",
  node_type: "input.audio",
  node_slot: "audio",
  // What the real endpoint answers for a dropped recording: reading it comes first, because the
  // repair and the mix work per beat and there are no beats until the words are measured.
  suggestions: [
    {
      node_type: "transcribe_audio",
      title: "Read what it says",
      why: "word-by-word timings off the recording. 16 kHz mono: the repair chain's band extension can put the top octaves back",
      to_slot: "audio",
      values: { engine: "faster_whisper", model: "base.en" },
    },
    {
      node_type: "qc_deliverable",
      title: "Check it against delivery",
      why: "integrated loudness, true peak and dead air",
      to_slot: "deliverable",
    },
  ],
};

/** What POST /v1/uploads answers for a dropped Matroska screen recording: an MP4. */
export const DROPPED_VIDEO = {
  asset_id: "ws_workspace01/originals-video/cd/" + "c".repeat(64) + ".mp4",
  filename: "2026-09-09 13-35-29.mp4",
  kind: "video" as const,
  mime: "video/mp4",
  size_bytes: 2_530_000,
  uploaded_bytes: 2_480_510,
  conversion: {
    action: "remux" as const,
    from_mime: "video/x-matroska",
    to_mime: "video/mp4",
    detail: "h264/aac copied into MP4 — no re-encode",
  },
  sha256: "c".repeat(64),
  facts: { duration_ms: 51_925, width: 1920, height: 1080, fps: 30.0 },
  description: "video · 1920x1080 · 30 fps · 51.9 s",
  node_type: "input.video",
  node_slot: "video",
  suggestions: [
    {
      node_type: "interpolate",
      title: "Smooth the motion",
      why: "30 fps in: GIMM-VFI fills between the frames",
      to_slot: "frames",
      values: { engine: "gimm_vfi" },
    },
  ],
};

/** Recorded upload filenames, reset per test. */
export const uploadPosts: string[] = [];

/** The stored Hugging Face token, as the server would hold it (never returned to the client). */
export const hfTokenStore: { token: string | null } = { token: null };

/** Recorded PUT /v1/prefs/theme bodies, reset per test. */
export const themePuts: unknown[] = [];

/** Recorded PUT bodies for the keymap and appearance preference keys, reset per test. */
export const keymapPuts: unknown[] = [];
export const appearancePuts: unknown[] = [];

/** Recorded POST /v1/comfy/models/download bodies; GET /downloads reflects them as running jobs. */
export const downloadPosts: { url: string; relative_path: string; filename: string }[] = [];

/** In-memory graph store mirroring /v1/graphs; reset per test via setup.ts. */
export const graphStore = new Map<string, Record<string, unknown>>();
export const graphRunPosts: string[] = [];

/** Verdicts the review panel recorded, in order. Reset per test via setup.ts. */
export const verdictPosts: { runId: string; body: Record<string, unknown> }[] = [];

/** AI reviews the panel asked for, in order. Reset per test via setup.ts. */
export const aiReviewPosts: { runId: string; body: Record<string, unknown> }[] = [];

/**
 * How the next `POST .../ai-review` answers.
 *
 * `"ok"` returns the stored opinion; `409` is "cannot be asked for" (a run holds the card) and
 * `502` "was asked and did not answer" — the two the UI has to show differently, because one is
 * fixed by waiting and the other by fixing the model stack.
 */
export const aiReviewMode = { next: "ok" as "ok" | 409 | 502 };

/** Reviews the fixture server has stored, by run id — so a refetch answers with them, the way
 *  the real route does once `reviews/frames/ai-review.json` is on disk. */
export const aiReviewStore = new Map<string, Record<string, unknown>>();

export const unauthenticated = () => http.get("*/v1/session", () => HttpResponse.json({ detail: "Not signed in" }, { status: 401 }));
export const authenticated = (session: Session = makeSession()) => http.get("*/v1/session", () => HttpResponse.json(session));

export const baseHandlers = [
  unauthenticated(),
  http.get("*/v1/meta", () => HttpResponse.json(META)),
  http.get("*/v1/action-items", () => HttpResponse.json([])),
  http.get("*/v1/runs", () => HttpResponse.json([])),
  http.get("*/v1/run-history", () => HttpResponse.json(HISTORY_RUNS)),
  // A run's detail carries that run's own row — its subject, lane and outcome — so a test that
  // switches runs sees a different run rather than the same one under a new id.
  http.get("*/v1/run-history/:runId", ({ params }) => {
    const row = HISTORY_RUNS.find((r) => r.run_id === String(params.runId));
    if (String(params.runId) === HISTORY_DETAIL.run_id) return HttpResponse.json(HISTORY_DETAIL);
    return HttpResponse.json({
      ...HISTORY_DETAIL,
      ...row,
      run_id: String(params.runId),
      outputs: [],
      outputs_total: 0,
      nodes: [],
      unattributed: 0,
      film: null,
      poster: null,
    });
  }),
  http.get("*/v1/run-history/:runId/review", ({ params }) => {
    const stored = aiReviewStore.get(String(params.runId));
    const page =
      params.runId === REVIEW_PAGE.run_id
        ? REVIEW_PAGE
        : { ...REVIEW_PAGE, run_id: String(params.runId), reviews: [] };
    return HttpResponse.json(stored ? { ...page, ai_reviews: stored } : page);
  }),
  // The verdict the panel sends is what the CLI would have written, so the fixture answers with
  // the batch as decided: accepted frames accepted, the rest untouched.
  http.post("*/v1/run-history/:runId/review", async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    verdictPosts.push({ runId: String(params.runId), body });
    const accept = new Set((body.accept as string[] | undefined) ?? []);
    const reject = new Set((body.reject as string[] | undefined) ?? []);
    const gate = REVIEW_PAGE.reviews[0]!;
    const frames = gate.frames.map((f) => ({
      ...f,
      verdict: reject.has(f.frame_id) ? "reject" : body.accept_rest || accept.has(f.frame_id) ? "accept" : "unreviewed",
      reason: reject.has(f.frame_id) ? String(body.reason ?? "") : "",
    }));
    const accepted = frames.filter((f) => f.verdict === "accept").length;
    const rejected = frames.filter((f) => f.verdict === "reject").length;
    return HttpResponse.json({
      ...REVIEW_PAGE,
      reviews: [
        {
          ...gate,
          frames,
          reviewer: body.reviewer ?? "operator",
          reviewed_at: "2026-09-10T20:00:00Z",
          accepted,
          rejected,
          unreviewed: frames.length - accepted - rejected,
          passed: rejected === 0 && accepted === frames.length,
        },
      ],
    });
  }),
  http.post("*/v1/run-history/:runId/ai-review", async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    aiReviewPosts.push({ runId: String(params.runId), body });
    if (aiReviewMode.next === 409) {
      return HttpResponse.json(
        { detail: "the GPU is busy with image-set. Review when the run finishes." },
        { status: 409 },
      );
    }
    if (aiReviewMode.next === 502) {
      return HttpResponse.json(
        { detail: "connection refused to 127.0.0.1:11434" },
        { status: 502 },
      );
    }
    const reviews = { [REVIEW_PAGE.reviews[0]!.deliverable]: AI_REVIEW };
    aiReviewStore.set(String(params.runId), reviews);
    return HttpResponse.json({ ...REVIEW_PAGE, ai_reviews: reviews });
  }),
  http.get("*/v1/comfy/models", () => HttpResponse.json(COMFY_INVENTORY)),
  http.get("*/v1/models/catalog", () =>
    HttpResponse.json({
      ...MODEL_CATALOG,
      jobs: installPosts.map((key, i) => ({
        key,
        kind: key.startsWith("skill:") ? "skill_env" : "weights",
        name: key,
        state: "running",
        detail: "",
        steps: [{ label: `download ${key}`, state: "running", detail: "" }],
        bytes_expected: 290_000_000,
        bytes_done: 29_000_000 * (i + 1),
        started_at: 1,
        finished_at: null,
      })),
    }),
  ),
  http.post("*/v1/models/install", async ({ request }) => {
    const body = (await request.json()) as { key: string };
    installPosts.push(body.key);
    return HttpResponse.json(
      {
        key: body.key,
        kind: "weights",
        name: body.key,
        state: "running",
        detail: "",
        steps: [],
        bytes_expected: 0,
        bytes_done: 0,
        started_at: 1,
        finished_at: null,
      },
      { status: 202 },
    );
  }),
  http.get("*/v1/models/jobs", () => HttpResponse.json([])),
  http.get("*/v1/models/hf-access", () =>
    HttpResponse.json({
      present: hfTokenStore.token !== null,
      handle: hfTokenStore.token !== null ? "huggingface.co" : "",
      stored_at: hfTokenStore.token !== null ? "2026-09-09T10:00:00Z" : null,
    }),
  ),
  http.put("*/v1/models/hf-access", async ({ request }) => {
    const body = (await request.json()) as { token: string };
    hfTokenStore.token = body.token;
    return new HttpResponse(null, { status: 204 });
  }),
  http.delete("*/v1/models/hf-access", () => {
    hfTokenStore.token = null;
    return new HttpResponse(null, { status: 204 });
  }),
  http.post("*/v1/uploads", async ({ request }) => {
    // jsdom's FormData does not round-trip a File's name through the multipart body (it arrives
    // as "blob"), and its content does not reach request.text() either. What does survive is the
    // part's declared Content-Type, so the fixture is chosen from that.
    const raw = await request.text();
    const name = raw.includes("svg")
      ? "logo.svg"
      : raw.includes("matroska")
        ? "recording.mkv"
        : "take one.wav";
    uploadPosts.push(name);
    if (name === "logo.svg") {
      return HttpResponse.json(
        { detail: "file type 'image/svg+xml' is not accepted (sniffed, not extension-based)" },
        { status: 422 },
      );
    }
    if (name === "recording.mkv") return HttpResponse.json(DROPPED_VIDEO, { status: 201 });
    return HttpResponse.json(DROPPED_AUDIO, { status: 201 });
  }),
  http.get("*/v1/graphs", () =>
    HttpResponse.json(
      [...graphStore.values()].map((doc) => ({
        graph_id: doc.graph_id,
        name: doc.name,
        nodes: (doc.nodes as unknown[]).length,
        links: (doc.links as unknown[]).length,
        updated_at: "2026-09-03T10:00:00Z",
      })),
    ),
  ),
  http.get("*/v1/graphs/:id", ({ params }) => {
    const doc = graphStore.get(String(params.id));
    return doc ? HttpResponse.json(doc) : HttpResponse.json({ detail: "not found" }, { status: 404 });
  }),
  http.put("*/v1/graphs/:id", async ({ params, request }) => {
    const doc = (await request.json()) as Record<string, unknown>;
    graphStore.set(String(params.id), doc);
    return HttpResponse.json({ graph_id: params.id });
  }),
  http.delete("*/v1/graphs/:id", ({ params }) => {
    graphStore.delete(String(params.id));
    return new HttpResponse(null, { status: 204 });
  }),
  http.post("*/v1/graphs/:id/runs", ({ params }) => {
    graphRunPosts.push(String(params.id));
    return HttpResponse.json(
      {
        run_id: "run-msw000001",
        ok: true,
        problems: [],
        deliverable_type: "short_video",
        dispositions: [],
        dag_nodes: 4,
      },
      { status: 202 },
    );
  }),
  http.post("*/v1/comfy/models/download", async ({ request }) => {
    const body = (await request.json()) as { url: string; relative_path: string; filename: string };
    downloadPosts.push(body);
    return HttpResponse.json(
      { job_id: `job_${downloadPosts.length}`, ...body, state: "running", detail: "transfer started", started_at: 1 },
      { status: 202 },
    );
  }),
  http.get("*/v1/comfy/models/downloads", () =>
    HttpResponse.json(
      downloadPosts.map((body, i) => ({
        job_id: `job_${i + 1}`,
        ...body,
        state: "running",
        detail: "transfer started",
        started_at: 1,
      })),
    ),
  ),
  http.post("*/v1/models/relink", () => {
    relinkPosts.push(1);
    return HttpResponse.json({
      index: ["video_generation/LTX-2.5:present"],
      comfy: ["vae/ltx.safetensors"],
      skipped: ["rife"],
    });
  }),
  http.get("*/v1/workspaces", () => HttpResponse.json(WORKSPACES)),
  http.get("*/v1/prefs/theme", () => HttpResponse.json({ value: null })),
  http.get("*/v1/prefs/locale", () => HttpResponse.json({ value: null })),
  http.put("*/v1/prefs/locale", () => new HttpResponse(null, { status: 204 })),
  http.get("*/v1/prefs/keymap", () => HttpResponse.json({ value: null })),
  http.get("*/v1/prefs/appearance", () => HttpResponse.json({ value: null })),
  http.put("*/v1/prefs/keymap", async ({ request }) => {
    keymapPuts.push(await request.json());
    return new HttpResponse(null, { status: 204 });
  }),
  http.put("*/v1/prefs/appearance", async ({ request }) => {
    appearancePuts.push(await request.json());
    return new HttpResponse(null, { status: 204 });
  }),
  http.get("*/v1/personas", () => HttpResponse.json([])),
  http.get("*/v1/engagement/inbox", () => HttpResponse.json([])),
  http.get("*/v1/sequences", () => HttpResponse.json([])),
  http.get("*/v1/brand-nodes", () => HttpResponse.json([])),
  http.get("*/v1/portal-links", () => HttpResponse.json([])),
  http.get("*/v1/portal-briefs", () => HttpResponse.json([])),
  http.put("*/v1/prefs/theme", async ({ request }) => {
    themePuts.push(await request.json());
    return new HttpResponse(null, { status: 204 });
  }),
  http.delete("*/v1/session", () => new HttpResponse(null, { status: 204 })),
  http.post("*/v1/session/workspace", async ({ request }) => {
    const { workspace_id } = (await request.json()) as { workspace_id: string };
    return HttpResponse.json(makeSession({ current_workspace_id: workspace_id }));
  }),
  ...productHandlers,
];
