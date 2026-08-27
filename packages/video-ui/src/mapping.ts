// Pure mapping from a RenderBundle timeline to renderable sequences (unit-testable without Remotion).
import type { CompiledScene, RenderBundle, SceneSpec } from "@content-factory/content-schema-ts";

export interface MappedScene {
  compiled: CompiledScene;
  /** The plan's SceneSpec, or null when the timeline references a scene the plan lacks. */
  spec: SceneSpec | null;
  from: number;
  durationInFrames: number;
}

export const IMPLEMENTED_KINDS = [
  "title",
  "section_intro",
  "big_number",
  "bullet_sequence",
  "source_card",
  "outro",
  "callout",
  "quote",
  "definition",
  "chapter_transition",
] as const satisfies readonly SceneSpec["kind"][];

export type ImplementedKind = (typeof IMPLEMENTED_KINDS)[number];

export function isImplementedKind(kind: string): kind is ImplementedKind {
  return (IMPLEMENTED_KINDS as readonly string[]).includes(kind);
}

export function mapTimeline(bundle: RenderBundle): MappedScene[] {
  const timeline = bundle.timeline;
  if (!timeline) throw new Error(`RenderBundle ${bundle.bundle_id} has no timeline`);
  const byId = new Map<string, SceneSpec>();
  for (const s of bundle.plan?.scenes ?? []) byId.set(s.scene_id, s);
  return timeline.scenes.map((compiled) => ({
    compiled,
    spec: byId.get(compiled.scene_id) ?? null,
    from: compiled.start_frame,
    durationInFrames: compiled.duration_frames,
  }));
}

/** Advisory checks (contiguity, coverage); rendering never depends on these passing. */
export function timelineIssues(bundle: RenderBundle): string[] {
  const issues: string[] = [];
  const timeline = bundle.timeline;
  if (!timeline) return ["no timeline"];
  let cursor = 0;
  for (const m of mapTimeline(bundle)) {
    if (m.from !== cursor) issues.push(`${m.compiled.scene_id} starts at ${m.from}, expected ${cursor}`);
    if (m.durationInFrames < 1) issues.push(`${m.compiled.scene_id} has no frames`);
    if (!m.spec) issues.push(`${m.compiled.scene_id} is not in the plan`);
    cursor = m.from + m.durationInFrames;
  }
  if (cursor !== timeline.total_frames) issues.push(`scenes cover ${cursor} frames, total_frames is ${timeline.total_frames}`);
  return issues;
}

/** A human-readable title for any scene kind (used by placeholders and QC). */
export function sceneTitle(scene: SceneSpec): string {
  switch (scene.kind) {
    case "title":
      return scene.title.text;
    case "section_intro":
      return scene.heading.text;
    case "big_number":
      return scene.label.text;
    case "chart":
    case "ranking":
    case "comparison":
    case "data_table":
    case "timeline":
    case "map":
    case "flow_diagram":
    case "relationship_diagram":
    case "bullet_sequence":
      return scene.title.text;
    case "image":
    case "screenshot":
    case "manim_asset":
      return scene.alt_text;
    case "quote":
      return scene.quote.text;
    case "definition":
      return scene.term.text;
    case "callout":
    case "outro":
      return scene.text.text;
    case "source_card":
      return `Sources (${scene.source_ids.length})`;
    case "chapter_transition":
      return scene.label.text;
    default:
      return (scene as { scene_id: string }).scene_id;
  }
}
