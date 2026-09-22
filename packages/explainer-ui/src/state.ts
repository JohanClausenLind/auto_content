// The pure state fold: (compiled scene, spec scene, frame) → what every entity looks like right now.
import type { Beat, CompiledExplainerScene, DiagramLayout, FilterAction, PixelBox, ResolvedAction, Scene, SortAction } from "@content-factory/content-schema-ts";
import { Easing } from "remotion";

import { IDENTITY_CAMERA, clamp01, entityBoxes, lerp, lerpCamera, panCamera, regionOf, safeAreaBox, zoomCamera, type Camera } from "./geometry";
import { DEEMPHASIS_ALPHA } from "./palette";
import { TOKENS } from "./tokens.gen";

export type Template = Scene["template"];
export type SpecAction = Beat["actions"][number];

export interface EntityState {
  /** 0 hidden … 1 shown; reveal raises it, hide lowers it. */
  visible: number;
  highlight: number;
  /** 0 … 1 towards the de-emphasis alpha; isolate and focus dim everything else. */
  dim: number;
  trace: number;
  /** 1 fully drawn; an entity some draw action targets starts at 0. */
  draw: number;
  flow: number;
  /** Seconds since the flow started, so a dash pattern moves at one speed at any fps. */
  flowSeconds: number;
}

export interface Annotation {
  entityId: string;
  text: string;
  progress: number;
}

export interface SortState {
  by: SortAction["by"];
  progress: number;
}

export interface FilterState {
  field: string;
  op: FilterAction["op"];
  value: number | string;
  progress: number;
}

/** Computed in Phase 2, rendered in Phase 3; scrollY needs the capture manifest and stays null. */
export interface SourceDocumentState {
  shown: number;
  sectionId: string | null;
  scrollProgress: number;
  scrollY: number | null;
  highlightQuote: string | null;
  quoteProgress: number;
}

export interface SceneState {
  entities: Record<string, EntityState>;
  annotations: Annotation[];
  focus: string | null;
  compare: [string, string] | null;
  compareProgress: number;
  sort: SortState | null;
  filter: FilterState | null;
  camera: Camera;
  sourceDocument: SourceDocumentState;
  deemphasisAlpha: number;
  /** The strongest highlight in the scene; everything not highlighted dims by it. */
  peakHighlight: number;
}

export interface ResolveOptions {
  layout?: DiagramLayout | null | undefined;
  canvas?: { width: number; height: number } | undefined;
}

export const HIDDEN_ENTITY: EntityState = { visible: 0, highlight: 0, dim: 0, trace: 0, draw: 1, flow: 0, flowSeconds: 0 };

const easeStandard = Easing.bezier(...TOKENS.motion.easing.standard);
const easeMove = Easing.bezier(...TOKENS.motion.easing.move);
const MOVE_ACTIONS: ReadonlySet<string> = new Set(["zoom_to", "pan_to", "sort", "filter", "isolate"]);
/** 24 px of air around a zoom target so its stroke never sits on the region edge. */
const ZOOM_PAD_PX = 24;

/** Raw 0..1 progress at a scene-relative frame; action frames are timeline-absolute. */
export function actionProgress(action: ResolvedAction, frameInScene: number, sceneStart: number): number {
  const start = action.start_frame - sceneStart;
  const span = Math.max(1, action.end_frame - action.start_frame);
  return clamp01((frameInScene - start) / span);
}

export function easeFor(action: string): (t: number) => number {
  return MOVE_ACTIONS.has(action) ? easeMove : easeStandard;
}

export function eased(action: string, raw: number): number {
  if (raw <= 0) return 0;
  if (raw >= 1) return 1;
  return easeFor(action)(raw);
}

export function templateEntityIds(template: Template): string[] {
  switch (template.template) {
    case "chart":
      return template.series.map((s) => s.entity_id);
    case "diagram":
      return [...template.nodes.map((n) => n.entity_id), ...template.edges.map((e) => e.entity_id)];
    case "text":
      return template.items.map((i) => i.entity_id);
    default:
      return [];
  }
}

/** Every entity a scene can address: the template's own plus every target, box and colour. */
export function sceneEntityIds(scene: Scene, compiled: CompiledExplainerScene): string[] {
  const ids = new Set<string>(templateEntityIds(scene.template));
  for (const action of compiled.actions) for (const target of action.targets) ids.add(target);
  for (const box of compiled.boxes) ids.add(box.entity_id);
  for (const color of compiled.colors) ids.add(color.entity_id);
  return [...ids];
}

/** Fold order: start frame, then index within the beat, then the beat's place in the spec. */
export function orderedActions(compiled: CompiledExplainerScene, beatRank: ReadonlyMap<string, number>): ResolvedAction[] {
  return [...compiled.actions].sort(
    (a, b) => a.start_frame - b.start_frame || a.index - b.index || (beatRank.get(a.beat_id) ?? 0) - (beatRank.get(b.beat_id) ?? 0),
  );
}

/** The spec action a resolved action came from: by (beat, index), else the beat's first of that kind. */
export function payloadFor(beats: readonly Beat[], action: ResolvedAction): SpecAction | null {
  const beat = beats.find((b) => b.beat_id === action.beat_id);
  if (!beat) return null;
  const byIndex = beat.actions[action.index];
  if (byIndex && byIndex.action === action.action) return byIndex;
  return beat.actions.find((a) => a.action === action.action) ?? null;
}

interface FoldContext {
  boxes: ReadonlyMap<string, PixelBox>;
  region: PixelBox;
  fps: number;
  frameInScene: number;
  sceneStart: number;
}

function entity(state: SceneState, id: string): EntityState {
  return (state.entities[id] ??= { ...HIDDEN_ENTITY });
}

function applyAction(state: SceneState, action: ResolvedAction, raw: number, p: number, payload: SpecAction | null, ctx: FoldContext): void {
  const targets = action.targets;
  const others = Object.keys(state.entities).filter((id) => !targets.includes(id));
  switch (action.action) {
    case "reveal":
      for (const id of targets) entity(state, id).visible = lerp(entity(state, id).visible, 1, p);
      break;
    case "hide":
      for (const id of targets) entity(state, id).visible = lerp(entity(state, id).visible, 0, p);
      break;
    case "highlight":
      for (const id of targets) entity(state, id).highlight = lerp(entity(state, id).highlight, 1, p);
      break;
    case "clear_highlight": {
      const ids = targets.length > 0 ? targets : Object.keys(state.entities);
      for (const id of ids) entity(state, id).highlight = lerp(entity(state, id).highlight, 0, p);
      // A bare clear, or one naming the focused entity, also releases the dim the focus imposed.
      if (targets.length === 0 || (state.focus !== null && targets.includes(state.focus))) {
        for (const e of Object.values(state.entities)) e.dim = lerp(e.dim, 0, p);
        state.focus = null;
      }
      break;
    }
    case "focus": {
      const [target] = targets;
      if (target === undefined) break;
      const previous = state.focus;
      const e = entity(state, target);
      e.highlight = lerp(e.highlight, 1, p);
      e.dim = lerp(e.dim, 0, p);
      for (const id of others) entity(state, id).dim = lerp(entity(state, id).dim, 1, p);
      if (previous !== null && previous !== target) entity(state, previous).highlight = lerp(entity(state, previous).highlight, 0, p);
      state.focus = target;
      break;
    }
    case "isolate":
      for (const id of targets) entity(state, id).dim = lerp(entity(state, id).dim, 0, p);
      for (const id of others) entity(state, id).dim = lerp(entity(state, id).dim, 1, p);
      break;
    case "trace": {
      const n = targets.length;
      targets.forEach((id, i) => {
        const local = eased("trace", clamp01(raw * n - i));
        const e = entity(state, id);
        e.trace = Math.max(e.trace, local);
        e.visible = lerp(e.visible, 1, local);
      });
      break;
    }
    case "draw":
      for (const id of targets) {
        const e = entity(state, id);
        e.draw = lerp(e.draw, 1, p);
        e.visible = lerp(e.visible, 1, p);
      }
      break;
    case "flow":
      for (const id of targets) {
        const e = entity(state, id);
        e.flow = lerp(e.flow, 1, p);
        e.visible = lerp(e.visible, 1, p);
        e.flowSeconds = (ctx.frameInScene - (action.start_frame - ctx.sceneStart)) / ctx.fps;
      }
      break;
    case "zoom_to": {
      const box = targets[0] === undefined ? undefined : ctx.boxes.get(targets[0]);
      if (box) state.camera = lerpCamera(state.camera, zoomCamera(box, ctx.region, ZOOM_PAD_PX), p);
      break;
    }
    case "pan_to": {
      const box = targets[0] === undefined ? undefined : ctx.boxes.get(targets[0]);
      if (box) state.camera = lerpCamera(state.camera, panCamera(box, ctx.region, state.camera.scale), p);
      break;
    }
    case "compare": {
      const [a, b] = targets;
      if (a !== undefined && b !== undefined) {
        state.compare = [a, b];
        state.compareProgress = p;
      }
      break;
    }
    case "annotate": {
      const [target] = targets;
      if (target !== undefined && payload?.action === "annotate") state.annotations.push({ entityId: target, text: payload.text, progress: p });
      break;
    }
    case "sort":
      if (payload?.action === "sort") state.sort = { by: payload.by, progress: p };
      break;
    case "filter":
      if (payload?.action === "filter") state.filter = { field: payload.field, op: payload.op, value: payload.value, progress: p };
      break;
    case "show_source":
      state.sourceDocument.shown = lerp(state.sourceDocument.shown, 1, p);
      break;
    case "scroll_to":
      if (payload?.action === "scroll_to") {
        state.sourceDocument.sectionId = payload.section_id;
        state.sourceDocument.scrollProgress = p;
      }
      break;
    case "focus_passage":
    case "highlight_quote":
      if (payload && (payload.action === "focus_passage" || payload.action === "highlight_quote")) {
        state.sourceDocument.highlightQuote = payload.quote_id;
        state.sourceDocument.quoteProgress = p;
      }
      break;
    default:
      break;
  }
}

/** The scene's state at `frameInScene`: a pure function, so seeking equals playing through. */
export function resolveSceneState(compiled: CompiledExplainerScene, scene: Scene, frameInScene: number, fps: number, options: ResolveOptions = {}): SceneState {
  const layout = options.layout ?? null;
  const canvas = options.canvas ?? TOKENS.layout.canvas;
  const drawTargets = new Set(compiled.actions.filter((a) => a.action === "draw").flatMap((a) => a.targets));
  const initial = new Set(scene.initial_visible);
  const entities: Record<string, EntityState> = {};
  for (const id of sceneEntityIds(scene, compiled)) {
    entities[id] = { ...HIDDEN_ENTITY, visible: initial.has(id) ? 1 : 0, draw: drawTargets.has(id) ? 0 : 1 };
  }
  const state: SceneState = {
    entities,
    annotations: [],
    focus: null,
    compare: null,
    compareProgress: 0,
    sort: null,
    filter: null,
    camera: IDENTITY_CAMERA,
    sourceDocument: { shown: 0, sectionId: null, scrollProgress: 0, scrollY: null, highlightQuote: null, quoteProgress: 0 },
    deemphasisAlpha: DEEMPHASIS_ALPHA,
    peakHighlight: 0,
  };
  const ctx: FoldContext = {
    boxes: entityBoxes(compiled, layout),
    region: regionOf(compiled, "plot", "content") ?? safeAreaBox(canvas.width, canvas.height),
    fps,
    frameInScene,
    sceneStart: compiled.start_frame,
  };
  const beatRank = new Map(scene.beats.map((b, i) => [b.beat_id, i] as const));
  for (const action of orderedActions(compiled, beatRank)) {
    if (frameInScene < action.start_frame - compiled.start_frame) continue;
    const raw = actionProgress(action, frameInScene, compiled.start_frame);
    applyAction(state, action, raw, eased(action.action, raw), payloadFor(scene.beats, action), ctx);
  }
  state.peakHighlight = Math.max(0, ...Object.values(state.entities).map((e) => e.highlight));
  return state;
}

export function entityState(state: SceneState, id: string): EntityState {
  return state.entities[id] ?? HIDDEN_ENTITY;
}

/** 1 at full strength down to the de-emphasis alpha when something else has the floor. */
export function entityDimFactor(state: SceneState, id: string): number {
  const e = entityState(state, id);
  const dim = Math.max(e.dim, state.peakHighlight * (1 - e.highlight));
  return 1 - dim * (1 - state.deemphasisAlpha);
}

/** Opacity an entity draws at: its visibility times its de-emphasis. */
export function entityOpacity(state: SceneState, id: string): number {
  return entityState(state, id).visible * entityDimFactor(state, id);
}
