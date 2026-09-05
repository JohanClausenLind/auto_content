/**
 * The workspace node catalogue: one node type per pipeline stage, plus the campaign brief as the
 * source node and a free-text note.
 *
 * `Record<Stage, …>` is deliberate — when a stage is added to the Pydantic contract and
 * regenerated, this file fails to typecheck until the stage has a node definition, so the
 * canvas can never silently lag the pipeline.
 */

import type { Stage } from "@content-factory/content-schema-ts";
import { createCatalog, type NodeDefinition } from "@content-factory/node-graph";

/**
 * LTX-2.5 generation sizes (multiples of 32, both portrait and landscape). 512x896 is the size
 * the proven showcase lane renders at on the 24 GB card; larger sizes lean on offload and the
 * x2 latent spatial upscaler.
 */
const LTX_SIZES = [
  "512x896",
  "576x1024",
  "768x1344",
  "896x512",
  "1024x576",
  "1344x768",
] as const;

type StageDef = Omit<NodeDefinition, "type" | "stage">;

const STAGE_DEFS: Record<Stage, StageDef> = {
  // --- shared -----------------------------------------------------------------------------
  ingest: {
    title: "Ingest Uploads",
    category: "research",
    summary: "Pulls uploaded files into the project with magic-number sniffing",
    inputs: [],
    outputs: [{ name: "sources", type: "SOURCES" }],
    widgets: [],
  },
  research: {
    title: "Research",
    category: "research",
    summary: "Gathers sources and extracts evidence behind the topic",
    executor: "ai",
    inputs: [
      { name: "brief", type: "BRIEF" },
      { name: "sources", type: "SOURCES", optional: true },
    ],
    outputs: [
      { name: "sources", type: "SOURCES" },
      { name: "evidence", type: "EVIDENCE" },
      { name: "claims", type: "CLAIMS" },
    ],
    widgets: [{ name: "depth", kind: "combo", default: "standard", options: ["quick", "standard", "deep"] }],
  },
  verify_claims: {
    title: "Verify Claims",
    category: "research",
    summary: "Blocks the run when script claims lack matching evidence",
    inputs: [{ name: "claims", type: "CLAIMS" }],
    outputs: [{ name: "claims", type: "CLAIMS" }],
    widgets: [],
  },
  compile_datasets: {
    title: "Compile Datasets",
    category: "research",
    summary: "Reproducible Polars transforms into typed dataset tables",
    inputs: [{ name: "evidence", type: "EVIDENCE", optional: true }],
    outputs: [{ name: "dataset", type: "DATASET" }],
    widgets: [],
  },
  plan_story: {
    title: "Plan Story",
    category: "story",
    summary: "Turns brief, claims and data into a beat-by-beat story plan",
    executor: "ai",
    inputs: [
      { name: "brief", type: "BRIEF" },
      { name: "claims", type: "CLAIMS", optional: true },
      { name: "dataset", type: "DATASET", optional: true },
    ],
    outputs: [{ name: "story", type: "STORY" }],
    widgets: [
      { name: "beats", kind: "int", default: 5, min: 1, max: 12 },
      {
        name: "story",
        kind: "text",
        default: "",
        placeholder: "fixtures/story/<plan>.json — a written script instead of a researched one",
        label: "story fixture",
      },
    ],
  },
  originality_topic: {
    title: "Originality: Topic",
    category: "story",
    summary: "Checks the topic against the archive before anything is made",
    inputs: [{ name: "brief", type: "BRIEF" }],
    outputs: [{ name: "verdict", type: "QC" }],
    widgets: [],
  },
  preflight: {
    title: "Preflight Gate",
    category: "story",
    summary: "Honest quote and capability check; waits for operator approval",
    help:
      "Prices the run, says which parts of it this machine can actually make, and then stops:" +
      " the workflow parks here until someone approves that exact revision. Approving a later" +
      " revision by mistake cannot happen — a stale approval is recorded and ignored.",
    executor: "human",
    inputs: [
      { name: "brief", type: "BRIEF" },
      { name: "story", type: "STORY", optional: true },
    ],
    outputs: [{ name: "approval", type: "QC" }],
    widgets: [],
  },
  // --- text -------------------------------------------------------------------------------
  write_copy: {
    title: "Write Copy",
    category: "text",
    summary: "Drafts platform copy variants from the story plan",
    executor: "ai",
    inputs: [{ name: "story", type: "STORY" }],
    outputs: [{ name: "text", type: "TEXT" }],
    // `variants` was declared and read by nothing: the stage writes exactly one payload, and a
    // second caption has no consumer anywhere downstream. Removed rather than faked.
    widgets: [
      { name: "tone", kind: "combo", default: "neutral", options: ["neutral", "playful", "direct", "formal"] },
    ],
  },
  compile_text_package: {
    title: "Compile Text Package",
    category: "text",
    summary: "Bundles copy into the deliverable package layout",
    inputs: [{ name: "text", type: "TEXT" }],
    outputs: [{ name: "package", type: "PACKAGE" }],
    widgets: [],
  },
  // --- static image -----------------------------------------------------------------------
  compile_artboards: {
    title: "Compile Artboards",
    category: "static",
    summary: "Lays the story out on typed artboards with brand tokens",
    inputs: [{ name: "story", type: "STORY" }],
    outputs: [{ name: "artboards", type: "ARTBOARD" }],
    widgets: [
      { name: "format", kind: "combo", default: "square", options: ["square", "portrait", "landscape", "story"] },
    ],
  },
  render_static: {
    title: "Render Static",
    category: "static",
    summary: "Renders artboards to pixel-perfect stills",
    inputs: [{ name: "artboards", type: "ARTBOARD" }],
    outputs: [{ name: "images", type: "IMAGE" }],
    widgets: [{ name: "scale", kind: "combo", default: "1x", options: ["1x", "2x"] }],
  },
  // --- carousel ---------------------------------------------------------------------------
  compile_cards: {
    title: "Compile Cards",
    category: "carousel",
    summary: "Splits the story into an ordered card sequence",
    inputs: [{ name: "story", type: "STORY" }],
    outputs: [{ name: "artboards", type: "ARTBOARD" }],
    // The card count is the carousel deliverable's own `card_count`, decided when the campaign
    // was compiled and used by write_copy to draft that many texts. A node widget that disagreed
    // with it could only be ignored, which is what it was.
    widgets: [],
  },
  render_cards: {
    title: "Render Cards",
    category: "carousel",
    summary: "Renders each card; one edited card re-renders exactly one card",
    inputs: [{ name: "artboards", type: "ARTBOARD" }],
    outputs: [{ name: "images", type: "IMAGE" }],
    widgets: [],
  },
  // --- article ----------------------------------------------------------------------------
  draft_article: {
    title: "Draft Article",
    category: "article",
    summary: "Long-form draft with citations wired to the claim gate",
    executor: "ai",
    inputs: [
      { name: "story", type: "STORY" },
      { name: "claims", type: "CLAIMS", optional: true },
    ],
    outputs: [{ name: "text", type: "TEXT" }],
    widgets: [{ name: "words", kind: "int", default: 900, min: 200, max: 4000, step: 100 }],
  },
  link_check: {
    title: "Link Check",
    category: "article",
    summary: "Verifies every outbound link before export",
    inputs: [{ name: "text", type: "TEXT" }],
    outputs: [{ name: "text", type: "TEXT" }],
    widgets: [],
  },
  compile_seo: {
    title: "Compile SEO",
    category: "article",
    summary: "Meta description, headings and slug from the draft",
    inputs: [{ name: "text", type: "TEXT" }],
    outputs: [{ name: "text", type: "TEXT" }],
    widgets: [{ name: "focus_keyword", kind: "text", default: "", placeholder: "optional focus keyword" }],
  },
  export_article: {
    title: "Export Article",
    category: "article",
    summary: "Drafts to WordPress, Ghost or Listmonk — publishing stays gated",
    inputs: [{ name: "text", type: "TEXT" }],
    outputs: [{ name: "package", type: "PACKAGE" }],
    widgets: [
      { name: "destination", kind: "combo", default: "wordpress", options: ["wordpress", "ghost", "listmonk"] },
    ],
  },
  // --- newsletter -------------------------------------------------------------------------
  compose_email: {
    title: "Compose Email",
    category: "newsletter",
    summary: "Email body from the story plan, unsubscribe block enforced",
    executor: "ai",
    inputs: [{ name: "story", type: "STORY" }],
    outputs: [{ name: "text", type: "TEXT" }],
    widgets: [],
  },
  compile_email: {
    title: "Compile Email",
    category: "newsletter",
    summary: "MJML-style compile with a plain-text alternative",
    inputs: [{ name: "text", type: "TEXT" }],
    outputs: [{ name: "package", type: "PACKAGE" }],
    widgets: [],
  },
  email_preview_qc: {
    title: "Email Preview QC",
    category: "newsletter",
    summary: "Renders previews and checks required footers",
    inputs: [{ name: "package", type: "PACKAGE" }],
    outputs: [{ name: "report", type: "QC" }],
    widgets: [],
  },
  // --- image sequence ---------------------------------------------------------------------
  review_assets: {
    title: "Review Assets",
    category: "image sequence",
    summary: "Checks each character sculpture and blocks until someone has approved it",
    executor: "human",
    inputs: [{ name: "shots", type: "SHOTS" }],
    outputs: [{ name: "shots", type: "SHOTS" }],
    widgets: [],
    keywords: ["asset", "sculpture", "character", "approval", "gate", "review"],
  },
  review_frames: {
    title: "Review Frames",
    category: "image sequence",
    summary: "Contact sheet + measured findings per drawing; blocks until a reviewer has looked",
    executor: "human",
    inputs: [{ name: "frames", type: "SEQUENCE,IMAGE" }],
    outputs: [{ name: "frames", type: "SEQUENCE,IMAGE" }],
    widgets: [],
    keywords: ["review", "contact sheet", "gate", "qc", "approval", "look"],
  },
  find_reference: {
    title: "Find Reference",
    category: "image sequence",
    summary:
      "Searches the reference library in words and picks the real interaction a shot is staged from",
    inputs: [{ name: "story", type: "STORY" }],
    outputs: [{ name: "reference", type: "SHOTS" }],
    widgets: [
      { name: "limit", kind: "int", default: 10, min: 1, max: 100 },
      {
        name: "affection",
        kind: "combo",
        default: "any",
        options: ["any", "affection", "neutral", "aggression", "staging"],
        label: "only this kind of interaction",
      },
      {
        name: "usage",
        kind: "combo",
        default: "pose_derivable",
        options: ["any", "pose_derivable", "pixels_usable", "reference_only"],
        label: "what the material must be good for",
      },
      { name: "require_pose", kind: "toggle", default: true, label: "only clips something can be driven from" },
    ],
    keywords: ["reference", "mocap", "search", "retrieval", "interaction", "library"],
  },
  plan_shots: {
    title: "Plan Shots",
    category: "image sequence",
    summary: "One 3D shot per story beat: camera path, character placement, frame count",
    inputs: [
      { name: "story", type: "STORY" },
      // What find_reference chose. Optional: with no reference library the lane still runs and
      // the preset planner stages the beats.
      { name: "reference", type: "SHOTS", optional: true },
    ],
    outputs: [{ name: "shots", type: "SHOTS" }],
    widgets: [
      {
        name: "planner",
        kind: "combo",
        default: "story_presets",
        options: ["story_presets", "fixture", "reference"],
      },
      {
        name: "fixture_path",
        kind: "text",
        default: "",
        placeholder: "fixtures/shots/<plan>.json (planner: fixture)",
      },
      { name: "size", kind: "combo", default: "1024x576", options: LTX_SIZES, label: "size (WxH)" },
      { name: "fps", kind: "combo", default: "24", options: ["24", "25", "30", "60"] },
    ],
    keywords: ["shot", "camera", "blender", "storyboard", "3d"],
  },
  route_shots: {
    title: "Route Shots",
    category: "image sequence",
    summary: "Hybrid workflow: per beat, deterministic (Remotion: D3 / Vega / MapLibre / Manim) or generative (Blender controls → HiDream → LTX)",
    inputs: [
      { name: "story", type: "STORY" },
      { name: "shots", type: "SHOTS" },
    ],
    outputs: [{ name: "routing", type: "SHOTS" }],
    widgets: [
      { name: "default_route", kind: "combo", default: "render", options: ["render", "generate"] },
      {
        name: "generate_kinds",
        kind: "text",
        default: "title, section_intro, chapter_transition, image, quote, callout, outro",
        label: "scene kinds routed to the generative chain",
      },
    ],
    keywords: ["router", "hybrid", "remotion", "generative", "deterministic", "ffmpeg"],
  },
  generate_anchor: {
    title: "Generate Anchor",
    category: "image sequence",
    summary: "The anchor image every frame must stay faithful to",
    executor: "ai",
    inputs: [
      { name: "story", type: "STORY" },
      { name: "controls", type: "CONTROLS", optional: true },
    ],
    outputs: [{ name: "anchor", type: "IMAGE" }],
    widgets: [
      { name: "prompt", kind: "textarea", default: "", placeholder: "Visual style, subject, mood…", required: true, rows: 4 },
      // The look the whole run is locked to. The stage already reads it; without a widget it was
      // settable from a workflow definition but invisible on the canvas.
      { name: "style", kind: "textarea", default: "", placeholder: "The look every frame shares…", rows: 3 },
      { name: "model", kind: "combo", default: "hidream-o1", options: ["hidream-o1", "krea2-turbo", "flux2-dev", "comfy-fixture", "mock"] },
      { name: "seed", kind: "seed", default: 0 },
      // HiDream-O1 snaps to its own ~4 MP buckets by aspect ratio, so this only bites on backends
      // that honour a size (Krea2 via ComfyUI, the fixture, the mock).
      { name: "megapixels", kind: "float", default: 1.0, min: 0.2, max: 2.0, step: 0.1, precision: 1, label: "megapixels (ignored by hidream-o1)" },
    ],
  },
  lock_generation: {
    title: "Generation Lock",
    category: "image sequence",
    summary: "Freezes model, seed and controls so frames cannot drift",
    inputs: [{ name: "anchor", type: "IMAGE" }],
    outputs: [{ name: "lock", type: "IMAGE" }],
    widgets: [],
  },
  compile_controls: {
    title: "Compile Controls",
    category: "image sequence",
    summary: "Per-frame control passes: 2D motion plan (builtin) or Blender scene (depth, normals, segmentation, skeleton, layout, rough RGB)",
    inputs: [
      { name: "shots", type: "SHOTS", optional: true },
      { name: "lock", type: "IMAGE", optional: true },
      { name: "story", type: "STORY", optional: true },
    ],
    outputs: [
      { name: "controls", type: "CONTROLS" },
      { name: "mask", type: "MASK" },
    ],
    widgets: [
      { name: "compiler", kind: "combo", default: "motion_plan", options: ["motion_plan", "blender"] },
      { name: "rough_rgb", kind: "toggle", default: true },
      { name: "depth", kind: "toggle", default: true },
      { name: "normals", kind: "toggle", default: true },
      { name: "segmentation", kind: "toggle", default: true },
      { name: "skeleton", kind: "toggle", default: true },
      { name: "canny", kind: "toggle", default: false },
    ],
    keywords: ["blender", "depth", "pose", "openpose", "segmentation", "controlnet"],
  },
  generate_keyframes: {
    title: "Generate Keyframes",
    category: "image sequence",
    summary: "Hub-and-spoke keyframes off the locked anchor",
    executor: "ai",
    inputs: [
      { name: "lock", type: "IMAGE" },
      { name: "controls", type: "CONTROLS" },
    ],
    outputs: [{ name: "frames", type: "SEQUENCE" }],
    // No widgets on purpose. The frame count comes from the control plan and the seed from the
    // generation lock, so a `frames` or `seed` widget here would look bound and do nothing --
    // the stage never reads either. If the length should become settable, the plan has to be
    // resampled first, and the widget can come back with the behaviour.
    widgets: [],
  },
  drift_qc: {
    title: "Drift QC",
    category: "image sequence",
    summary: "Rejects frames that drift off the anchor; bounded regeneration",
    inputs: [{ name: "frames", type: "SEQUENCE" }],
    outputs: [{ name: "frames", type: "SEQUENCE" }],
    widgets: [],
  },
  interpolate: {
    title: "Interpolate",
    category: "image sequence",
    summary: "Fills between frames for smooth motion (Practical-RIFE fast tier, GIMM-VFI quality tier)",
    inputs: [{ name: "frames", type: "SEQUENCE,VIDEO" }],
    outputs: [{ name: "frames", type: "SEQUENCE" }],
    widgets: [
      { name: "factor", kind: "combo", default: "2x", options: ["2x", "4x"] },
      // none: leave the cut alone. Smoothing a held-drawing sequence invents the in-between
      // frames that the held cut exists to avoid.
      { name: "engine", kind: "combo", default: "rife", options: ["rife", "gimm_vfi", "none"] },
    ],
    keywords: ["rife", "gimm", "vfi", "frame interpolation"],
  },
  fix_video: {
    title: "Fix Video (Cutie + ProPainter)",
    category: "video",
    summary: "Tracks chosen segmentation ids with Cutie and inpaints them away with ProPainter; a no-op until ids are chosen",
    inputs: [
      { name: "frames", type: "SEQUENCE,VIDEO" },
      { name: "mask", type: "MASK", optional: true },
    ],
    outputs: [{ name: "frames", type: "SEQUENCE" }],
    widgets: [
      { name: "remove_ids", kind: "text", default: "", placeholder: "segmentation ids to remove, e.g. 2,3" },
      { name: "mask_dilation", kind: "int", default: 4, min: 0, max: 64 },
    ],
    keywords: ["cutie", "propainter", "inpaint", "mask", "remove"],
  },
  upscale_video: {
    title: "Upscale (SeedVR2)",
    category: "video",
    summary: "SeedVR2 7B restoration + upscale of a frame sequence, PNG out",
    inputs: [{ name: "frames", type: "SEQUENCE,VIDEO" }],
    outputs: [{ name: "frames", type: "SEQUENCE" }],
    widgets: [
      { name: "resolution", kind: "combo", default: "1080", options: ["720", "1080", "1440", "2160"] },
    ],
    keywords: ["seedvr2", "upscale", "restore"],
  },
  package_sequence: {
    title: "Package Sequence",
    category: "image sequence",
    summary: "Contact sheet, MP4 preview or print flipbook PDF",
    inputs: [{ name: "frames", type: "SEQUENCE" }],
    outputs: [{ name: "package", type: "PACKAGE" }],
    // Contact sheet, mp4 preview and flipbook PDF are all written every time: they are what the
    // frame-review gate shows a person, they cost seconds, and different reviewers want different
    // ones. A single-choice widget could only take two of them away.
    widgets: [],
  },
  // --- audio ------------------------------------------------------------------------------
  lock_script: {
    title: "Lock Script",
    category: "audio",
    summary: "Freezes the narration script so timings stay valid",
    // What it actually freezes is the story plan's narration; a copy draft can feed it instead,
    // which is what the researched lanes do.
    inputs: [
      { name: "story", type: "STORY", optional: true },
      { name: "text", type: "TEXT", optional: true },
    ],
    outputs: [{ name: "script", type: "SCRIPT" }],
    widgets: [],
  },
  synthesize_narration: {
    title: "Synthesize Narration",
    category: "audio",
    summary: "Text-to-speech, timed per word — by the model where it can, by forced alignment where it cannot",
    executor: "ai",
    inputs: [{ name: "script", type: "SCRIPT" }],
    outputs: [{ name: "audio", type: "AUDIO" }],
    widgets: [
      {
        name: "voice",
        kind: "combo",
        default: "mock",
        options: ["mock", "qwen3tts", "kokoro-82m", "elevenlabs"],
      },
      {
        name: "speaker",
        kind: "combo",
        default: "ryan",
        options: ["ryan", "aiden", "vivian", "serena", "uncle_fu", "dylan", "eric", "ono_anna", "sohee"],
      },
      {
        name: "instruct",
        kind: "text",
        default: "",
        placeholder: "delivery note, e.g. Calm documentary narrator, unhurried",
      },
      {
        name: "aligner",
        kind: "combo",
        default: "faster_whisper",
        options: ["faster_whisper", "even_split", "whisperx"],
      },
      { name: "speed", kind: "float", default: 1.0, min: 0.5, max: 1.5, step: 0.05, precision: 2 },
    ],
    keywords: ["tts", "voice", "narration", "qwen", "kokoro", "timbre", "alignment"],
  },
  voice_over: {
    title: "Voice Over (recorded)",
    category: "audio",
    summary: "Human takes from <takes_dir>/<beat_id>.wav, force-aligned to the locked script",
    inputs: [{ name: "script", type: "SCRIPT" }],
    outputs: [{ name: "audio", type: "AUDIO" }],
    widgets: [
      {
        name: "takes_dir",
        kind: "text",
        default: "takes",
        placeholder: "recordings folder — relative to the run's project, or an absolute path",
      },
      {
        name: "aligner",
        kind: "combo",
        default: "even_split",
        options: ["even_split", "faster_whisper", "whisperx"],
      },
    ],
    keywords: ["human", "recording", "take", "voice", "alignment", "whisper", "dub"],
  },
  restore_speech: {
    title: "Restore Speech",
    category: "audio",
    summary:
      "Voice chain per beat: detect artifacts, optional ClearerVoice cleanup and 48 kHz band extension, Resemble Enhance restoration, then de-esser, EQ and light compression",
    inputs: [{ name: "audio", type: "AUDIO" }],
    outputs: [{ name: "audio", type: "AUDIO" }],
    widgets: [
      { name: "cleanup", kind: "combo", default: "off", options: ["off", "clearervoice"] },
      {
        name: "band_extension",
        kind: "combo",
        default: "off",
        options: ["off", "clearervoice_sr"],
      },
      { name: "enhancer", kind: "combo", default: "off", options: ["off", "resemble_enhance"] },
      { name: "enhancer_mode", kind: "combo", default: "enhance", options: ["enhance", "denoise"] },
      { name: "enhancer_nfe", kind: "int", default: 32, min: 1, max: 128 },
      { name: "gate", kind: "combo", default: "detected", options: ["detected", "always"] },
      { name: "device", kind: "combo", default: "cpu", options: ["cpu", "cuda"] },
    ],
    keywords: [
      "restore",
      "denoise",
      "enhance",
      "de-ess",
      "eq",
      "compress",
      "super-resolution",
      "resemble",
      "clearervoice",
      "mossformer",
      "artifact",
      "metallic",
      "muffled",
    ],
  },
  sound_design: {
    title: "Sound Design (MMAudio)",
    category: "audio",
    summary: "Watches the silent cut and writes SFX that land on the frame; the mix beds it under speech",
    executor: "ai",
    inputs: [
      { name: "video", type: "VIDEO", optional: true },
      { name: "frames", type: "SEQUENCE", optional: true },
    ],
    outputs: [{ name: "sfx", type: "AUDIO" }],
    widgets: [
      { name: "backend", kind: "combo", default: "mock", options: ["mock", "mmaudio"] },
      {
        name: "prompt",
        kind: "textarea",
        default: "",
        placeholder: "footsteps on wet stone, sea wind, cloth rustle…",
        rows: 3,
      },
      { name: "negative_prompt", kind: "text", default: "", placeholder: "music, speech" },
      { name: "gain_db", kind: "float", default: -22, min: -60, max: 0, step: 1, precision: 0 },
      { name: "steps", kind: "int", default: 25, min: 1, max: 200 },
      { name: "seed", kind: "seed", default: 0 },
    ],
    keywords: ["mmaudio", "foley", "sfx", "sound effects", "audio"],
  },
  align_words: {
    title: "Align Words",
    category: "audio",
    summary: "Validates word timings against the locked script",
    inputs: [
      { name: "audio", type: "AUDIO" },
      { name: "script", type: "SCRIPT" },
    ],
    outputs: [{ name: "timings", type: "DATASET" }],
    widgets: [],
  },
  compile_captions: {
    title: "Compile Captions",
    category: "audio",
    summary: "SRT/WebVTT from measured word timings",
    inputs: [{ name: "timings", type: "DATASET" }],
    outputs: [{ name: "captions", type: "CAPTIONS" }],
    // SRT, WebVTT and the styled ASS burn-in track are all written every time: the sidecars cost
    // nothing, each destination package picks the one it wants, and compose_video needs the SRT to
    // burn from. Choosing one would only break the others.
    widgets: [],
  },
  select_music: {
    title: "Select Music",
    category: "audio",
    summary: "Deterministic pick from the local music library, attribution included",
    inputs: [{ name: "story", type: "STORY", optional: true }],
    outputs: [{ name: "music", type: "AUDIO" }],
    widgets: [
      { name: "mood", kind: "combo", default: "documentary", options: ["calm", "neutral", "documentary", "energetic"] },
      { name: "gain_db", kind: "float", default: -18, min: -36, max: 0, step: 1, precision: 0 },
    ],
  },
  mix_audio: {
    title: "Mix Audio",
    category: "audio",
    summary: "Music bed ducked under narration; two-pass loudnorm master to -14 LUFS / -1 dBTP",
    inputs: [
      { name: "audio", type: "AUDIO" },
      { name: "music", type: "AUDIO", optional: true },
      { name: "sfx", type: "AUDIO", optional: true },
    ],
    outputs: [{ name: "audio", type: "AUDIO" }],
    widgets: [{ name: "target_lufs", kind: "float", default: -14, min: -24, max: -9, step: 0.5, precision: 1 }],
  },
  // --- video ------------------------------------------------------------------------------
  compile_timeline: {
    title: "Compile Timeline",
    category: "video",
    summary: "Milliseconds to integer frames; narration drives scene lengths",
    inputs: [
      { name: "story", type: "STORY" },
      { name: "timings", type: "DATASET", optional: true },
    ],
    outputs: [{ name: "timeline", type: "TIMELINE" }],
    widgets: [{ name: "fps", kind: "int", default: 30, min: 12, max: 60 }],
  },
  render_animation: {
    title: "Render Animation",
    category: "video",
    summary: "Deterministic explainer animation (builtin renderer; Manim skill opt-in)",
    inputs: [{ name: "story", type: "STORY", optional: true }],
    outputs: [{ name: "frames", type: "SEQUENCE" }],
    widgets: [
      { name: "kind", kind: "combo", default: "count_up", options: ["count_up", "equation", "diagram_build"] },
      { name: "duration_s", kind: "float", default: 2.0, min: 0.5, max: 60, step: 0.5, precision: 1 },
      { name: "fps", kind: "combo", default: "24", options: ["24", "30"] },
      // The frame was hardcoded at 1280x720, so a vertical maths short was unreachable.
      { name: "size", kind: "combo", default: "1280x720", options: ["1280x720", "1920x1080", "1080x1920", "1080x1080"], label: "size (WxH)" },
    ],
    keywords: ["manim", "math", "diagram", "explainer"],
  },
  render_scenes: {
    title: "Render Scenes",
    category: "video",
    summary: "Remotion renders every scene deterministically",
    inputs: [{ name: "timeline", type: "TIMELINE" }],
    outputs: [{ name: "frames", type: "SEQUENCE" }],
    widgets: [],
  },
  generate_video: {
    title: "Image to Video (LTX-2.5)",
    category: "video",
    summary: "LTX-2.5 invents the motion between the drawings — or, with motion: hold, each drawing is simply held for its shot and cut to the next",
    executor: "ai",
    inputs: [
      { name: "first_frame", type: "IMAGE" },
      { name: "last_frame", type: "IMAGE", optional: true },
      { name: "controls", type: "CONTROLS", optional: true },
    ],
    outputs: [
      { name: "video", type: "VIDEO" },
      { name: "audio", type: "AUDIO" },
    ],
    widgets: [
      {
        name: "prompt",
        kind: "textarea",
        default: "",
        placeholder: "Visual style, scene overview, storyboard beats…",
        required: true,
        rows: 5,
      },
      // hold needs no model at all: the drawings are cut together, each held for its shot's
      // length, and every frame on screen is one you approved. The jump between them is the look.
      { name: "motion", kind: "combo", default: "ltx", options: ["ltx", "hold"] },
      { name: "size", kind: "combo", default: "512x896", options: LTX_SIZES, label: "size (WxH)" },
      { name: "duration", kind: "float", default: 3.0, min: 1, max: 10, step: 0.5, precision: 1 },
      { name: "guide_strength", kind: "float", default: 1.0, min: 0, max: 10, step: 0.1, precision: 1, label: "keyframe guide strength" },
      { name: "noise_seed", kind: "seed", default: 0 },
      {
        name: "unet_name",
        kind: "combo",
        default: "ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf",
        options: ["ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf"],
      },
      {
        name: "clip_name",
        kind: "combo",
        default: "gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf",
        options: ["gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf"],
      },
      {
        name: "vae_name",
        kind: "combo",
        default: "ltx-2.5-video-vae-conv-bf16.safetensors",
        options: ["ltx-2.5-video-vae-conv-bf16.safetensors"],
      },
      // `audio_vae` was here and there is no audio branch in the i2v graph to load it into:
      // shots are generated silent and the sound is designed against the cut (sound_design ->
      // mix_audio). It would have been a weight name nothing loaded.
    ],
    keywords: ["ltx", "ltx-2.5", "gguf", "i2v", "comfyui", "video generation"],
    width: 320,
  },
  compose_video: {
    title: "Compose Video",
    category: "video",
    summary: "Mux frames, narration and captions into the final clip; with a shot routing, interleave Remotion segments and generated clips in beat order",
    inputs: [
      { name: "frames", type: "SEQUENCE" },
      { name: "audio", type: "AUDIO", optional: true },
      { name: "captions", type: "CAPTIONS", optional: true },
      { name: "clips", type: "VIDEO", optional: true },
      { name: "routing", type: "SHOTS", optional: true },
    ],
    outputs: [{ name: "video", type: "VIDEO" }],
    // Delivery is H.264 in MP4 throughout: the artifact store, the ffprobe QC, the caption burn
    // and every destination package assume it. VP9 and ProRes are a delivery-format decision (a
    // different container, different QC limits, different packages), not a knob on one node.
    widgets: [],
  },
  // --- delivery ---------------------------------------------------------------------------
  qc_deliverable: {
    title: "QC Deliverable",
    category: "delivery",
    summary: "Checks the finished file against what the campaign promised",
    help:
      "Reads the finished deliverable out of the run's folder and tests the promises made about" +
      " it: that an animated explainer actually moves rather than cross-fading between stills," +
      " that nothing strobes fast enough to be a seizure risk, that alt text and reading order" +
      " exist, that no caption or on-screen line carries a credential, and that every chart names" +
      " a dataset that exists. The verdict is written to qc/report.json and Destination Packages" +
      " stamps it into each package. A failing layer fails the stage, so nothing downstream ships" +
      " a deliverable that broke its own promise.",
    inputs: [
      {
        name: "deliverable",
        type: "VIDEO,IMAGE,SEQUENCE,AUDIO,TEXT,PACKAGE",
        hint: "connect the finished thing: the cut from Compose Video, the frames from"
          + " Interpolate or Upscale, the image from Generate Anchor, the master from Mix Audio."
          + " The wire is what puts this after the work it inspects",
      },
    ],
    outputs: [{ name: "report", type: "QC" }],
    widgets: [],
  },
  originality_gate: {
    title: "Originality Gate",
    category: "delivery",
    summary: "Typed decision; a model can never override a block",
    help:
      "Compares the finished deliverable with everything this workspace has already made — text" +
      " and frame shingles, hook and beat structure, perceptual hashes — and returns one typed" +
      " decision from ORIGINAL to MASS_PRODUCTION_RISK, with the evidence attached. A block is a" +
      " block: no model output can lift it.",
    inputs: [
      {
        name: "deliverable",
        type: "VIDEO,IMAGE,SEQUENCE,AUDIO,TEXT,PACKAGE",
        hint: "connect the finished thing, the same output QC Deliverable reads",
      },
    ],
    outputs: [{ name: "verdict", type: "QC" }],
    widgets: [],
  },
  compile_destination_packages: {
    title: "Destination Packages",
    category: "delivery",
    summary: "One package per destination: the files, their digests and the QC verdict",
    help:
      "Turns one finished deliverable into one package per destination the campaign declares:" +
      " the files that make it up with a digest each, the visibility asked for, and whether QC" +
      " passed — written to destination-packages/packages.json. This is the last step before a" +
      " platform could see anything, and it uploads nothing: publishing is a separate," +
      " approval-gated flow. With no destinations configured it still packages once, for the" +
      " run's own output folder.",
    inputs: [
      {
        name: "deliverable",
        type: "VIDEO,IMAGE,SEQUENCE,AUDIO,TEXT,PACKAGE",
        hint: "connect the finished thing; the QC input beside it takes the report so the"
          + " verdict is stamped into every package",
      },
      { name: "qc", type: "QC", optional: true },
    ],
    outputs: [{ name: "packages", type: "DELIVERABLE" }],
    widgets: [],
  },
  package_qc: {
    title: "Package QC",
    category: "delivery",
    // Do not describe this as checking platform limits: its executor writes {"passed": true} and
    // checks nothing. Saying otherwise in the panel would be the exact dishonesty the QC nodes
    // exist to prevent.
    summary: "Placeholder: writes a passing report without checking anything yet",
    help:
      "Not implemented. The stage exists and runs, but it writes qc/package.json with" +
      ' {"passed": true} and inspects nothing, so a green Package QC means only that the stage' +
      " ran. What it is meant to do, once written, is re-check each built package against the" +
      " declared capabilities of its destination — text length, image count and size, whether" +
      " the visibility is one that platform supports — which the distribution backends already" +
      " carry. Until then the real check on a package is the publisher's own validate() at" +
      " publish time, and QC Deliverable is what actually inspects the film.",
    inputs: [
      {
        name: "packages",
        type: "DELIVERABLE",
        hint: "connect Destination Packages — this checks the parcels, not the film",
      },
    ],
    outputs: [{ name: "report", type: "QC" }],
    widgets: [],
  },
};


/**
 * Non-stage nodes: the campaign brief feeds the graph, notes are text on the canvas, and the
 * comfy.* / publish.* nodes stand for the local ComfyUI generation path and the Tier-1
 * distribution path. Model filename options are the allowlisted local files the inventory
 * endpoint (`/v1/comfy/models`) verifies.
 */
const EXTRA_DEFS: readonly NodeDefinition[] = [
  {
    type: "publish.social",
    title: "Publish to Social",
    category: "delivery",
    summary: "Publishes exactly once via the idempotent publisher — approval-gated, honours the kill switch",
    executor: "hybrid",
    inputs: [{ name: "packages", type: "DELIVERABLE" }],
    outputs: [],
    widgets: [
      {
        name: "destination",
        kind: "combo",
        default: "bluesky",
        options: ["bluesky", "mastodon", "discord-webhook"],
      },
      { name: "profile", kind: "text", default: "", placeholder: "distribution profile id" },
      { name: "require_approval", kind: "toggle", default: true },
    ],
    keywords: ["bluesky", "mastodon", "discord", "post", "distribution"],
  },
  {
    type: "input.brief",
    title: "Campaign Brief",
    category: "input",
    summary: "The topic, audience and quality everything downstream consumes",
    inputs: [],
    outputs: [{ name: "brief", type: "BRIEF" }],
    widgets: [
      { name: "topic", kind: "textarea", default: "", placeholder: "What is this campaign about?", required: true, rows: 3 },
      { name: "audience", kind: "text", default: "", placeholder: "who it is for" },
      { name: "quality", kind: "combo", default: "demo", options: ["smoke", "demo"] },
    ],
    keywords: ["campaign", "topic", "start"],
    width: 300,
  },
  {
    // Litegraph-shaped terminal, the counterpart to the brief. Every lane used to end on an
    // output nothing consumed — the canvas called it an unused output and the operator had no
    // way to see where the files went. This node is that answer, on the canvas.
    type: "output.deliverables",
    title: "Deliverables",
    category: "delivery",
    summary: "Where the finished files land: the run's deliverable folder",
    help:
      "A terminal marker rather than a step. The run writes its files into the deliverable" +
      " folder in the artifact store whether or not this node is here; wiring the last thing you" +
      " care about into it — the destination packages, the master, the caption file — is how the" +
      " graph says which of them is the point. It copies, uploads and changes nothing.",
    inputs: [
      { name: "packages", type: "DELIVERABLE", optional: true },
      {
        name: "files",
        type: "VIDEO,IMAGE,SEQUENCE,AUDIO,TEXT,CAPTIONS,PACKAGE,QC",
        optional: true,
      },
    ],
    outputs: [],
    widgets: [],
    keywords: ["output", "save", "deliver", "finish", "end", "files"],
    width: 260,
  },
  {
    // The dropped-file nodes. Their widgets are written by the drop, not typed by hand: `asset`
    // is a content-addressed artifact key, which is why the file cannot be swapped under the
    // graph and why the node carries no path an operator could point somewhere else.
    type: "input.audio",
    title: "Audio File",
    category: "input",
    summary: "A recording you dropped on the canvas",
    help:
      "The file is already stored: dropping it uploaded it, sniffed what it actually is from" +
      " the bytes, and measured it. On Run it is written into the project's uploads folder," +
      " where the Ingest stage turns it into a typed source. What it cannot do yet is stand in" +
      " for a per-beat take — Voice Over still reads recordings named by beat id — so a clip" +
      " dropped here reaches the run as a source file, not as narration.",
    inputs: [],
    outputs: [{ name: "audio", type: "AUDIO" }],
    widgets: [
      { name: "asset", kind: "text", default: "", placeholder: "set by the drop", label: "artifact key", required: true },
      { name: "filename", kind: "text", default: "", placeholder: "name in the uploads folder" },
      { name: "bytes", kind: "int", default: 0, min: 0, max: 2147483647, label: "size (bytes)" },
    ],
    keywords: ["upload", "drop", "recording", "voice", "take", "wav", "mp3", "file"],
    width: 300,
  },
  {
    type: "input.image",
    title: "Image File",
    category: "input",
    summary: "A still you dropped on the canvas",
    help:
      "Stored, sniffed and measured when you dropped it. On Run it lands in the project's" +
      " uploads folder for the Ingest stage. Wire it into Image to Video to use it as a first" +
      " frame — the one thing no node could express before it existed.",
    inputs: [],
    outputs: [{ name: "image", type: "IMAGE" }],
    widgets: [
      { name: "asset", kind: "text", default: "", placeholder: "set by the drop", label: "artifact key", required: true },
      { name: "filename", kind: "text", default: "", placeholder: "name in the uploads folder" },
      { name: "bytes", kind: "int", default: 0, min: 0, max: 2147483647, label: "size (bytes)" },
    ],
    keywords: ["upload", "drop", "photo", "still", "png", "jpg", "frame", "file"],
    width: 300,
  },
  {
    type: "input.video",
    title: "Video File",
    category: "input",
    summary: "A clip you dropped on the canvas",
    help:
      "Stored, sniffed and measured when you dropped it. On Run it lands in the project's" +
      " uploads folder for the Ingest stage. Wire it into Interpolate, Upscale, Fix Video or" +
      " Sound Design to work on footage you already have.",
    inputs: [],
    outputs: [{ name: "video", type: "VIDEO" }],
    widgets: [
      { name: "asset", kind: "text", default: "", placeholder: "set by the drop", label: "artifact key", required: true },
      { name: "filename", kind: "text", default: "", placeholder: "name in the uploads folder" },
      { name: "bytes", kind: "int", default: 0, min: 0, max: 2147483647, label: "size (bytes)" },
    ],
    keywords: ["upload", "drop", "clip", "footage", "mp4", "mov", "file"],
    width: 300,
  },
  {
    type: "utility.note",
    title: "Note",
    category: "utility",
    summary: "Free text anywhere on the canvas",
    kind: "note",
    inputs: [],
    outputs: [],
    widgets: [],
    keywords: ["text", "comment", "markdown"],
    width: 280,
  },
];

export const WORKSPACE_DEFS: readonly NodeDefinition[] = [
  ...EXTRA_DEFS,
  ...(Object.entries(STAGE_DEFS) as [Stage, StageDef][]).map(([stage, def]) => ({
    ...def,
    type: stage,
    stage,
  })),
];

export const workspaceCatalog = createCatalog(WORKSPACE_DEFS);
