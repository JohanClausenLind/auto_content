import type { Beat, CompiledExplainerScene, ResolvedAction, Scene } from "@content-factory/content-schema-ts";
import { describe, expect, it } from "vitest";

import { MAX_ZOOM } from "../src/geometry";
import { DEEMPHASIS_ALPHA } from "../src/palette";
import { entityOpacity, entityState, resolveSceneState } from "../src/state";

const A = "ent_aaaaaaaa";
const B = "ent_bbbbbbbb";
const EDGE = "ent_edgeedge";
const BEAT = "beat_00000001";
const SCENE_START = 100;
const FPS = 30;

function action(kind: string, start: number, end: number, targets: string[], index = 0): ResolvedAction {
  return { beat_id: BEAT, index, action: kind, start_frame: SCENE_START + start, end_frame: SCENE_START + end, targets };
}

function fixture(actions: ResolvedAction[], initial: string[] = [A, B], beatActions?: Beat["actions"]): { compiled: CompiledExplainerScene; scene: Scene } {
  const compiled: CompiledExplainerScene = {
    scene_id: "scn_state0001",
    start_frame: SCENE_START,
    duration_frames: 200,
    regions: [{ name: "plot", box: { x: 0, y: 0, width: 1920, height: 1080 } }],
    colors: [],
    boxes: [{ entity_id: A, box: { x: 900, y: 500, width: 120, height: 80 }, font_px: null, lines: 1, text: null }],
    actions,
  };
  const scene: Scene = {
    scene_id: "scn_state0001",
    section: "build_model",
    purpose: "state fold under test",
    template: {
      template: "diagram",
      direction: "LR",
      nodes: [
        { entity_id: A, label: "A" },
        { entity_id: B, label: "B" },
      ],
      edges: [{ entity_id: EDGE, source_entity_id: A, target_entity_id: B, label: "" }],
    },
    layout: "primary",
    initial_visible: initial,
    beats: [{ beat_id: BEAT, cue: { segment_id: "seg_00000001", token_start: 0, token_end: 1, relation: "on", duration_class: "short" }, actions: beatActions ?? [{ action: "hold" }] }],
    claim_ids: [],
    source_ids: [],
    sponsored: false,
  };
  return { compiled, scene };
}

describe("resolveSceneState", () => {
  it("reveals from 0 through an eased middle to 1, on scene-relative frames", () => {
    const { compiled, scene } = fixture([action("reveal", 0, 12, [A])], []);
    const at = (f: number) => entityState(resolveSceneState(compiled, scene, f, FPS), A).visible;
    expect(at(0)).toBe(0);
    expect(at(6)).toBeGreaterThan(0);
    expect(at(6)).toBeLessThan(1);
    expect(at(12)).toBe(1);
    expect(at(40)).toBe(1);
  });

  it("hides after a reveal and stays hidden", () => {
    const { compiled, scene } = fixture([action("reveal", 0, 12, [A]), action("hide", 30, 42, [A])], []);
    const at = (f: number) => entityState(resolveSceneState(compiled, scene, f, FPS), A).visible;
    expect(at(20)).toBe(1);
    expect(at(36)).toBeGreaterThan(0);
    expect(at(36)).toBeLessThan(1);
    expect(at(60)).toBe(0);
  });

  it("highlights, dims the rest to the de-emphasis alpha, then clear_highlight releases both", () => {
    const { compiled, scene } = fixture([action("highlight", 0, 6, [A]), action("clear_highlight", 20, 26, [])]);
    const lit = resolveSceneState(compiled, scene, 10, FPS);
    expect(entityState(lit, A).highlight).toBe(1);
    expect(entityOpacity(lit, A)).toBe(1);
    expect(entityOpacity(lit, B)).toBeCloseTo(DEEMPHASIS_ALPHA, 10);
    const cleared = resolveSceneState(compiled, scene, 40, FPS);
    expect(entityState(cleared, A).highlight).toBe(0);
    expect(entityOpacity(cleared, B)).toBe(1);
  });

  it("isolate dims every other entity and leaves the target at full strength", () => {
    const { compiled, scene } = fixture([action("isolate", 0, 18, [A])]);
    const state = resolveSceneState(compiled, scene, 30, FPS);
    expect(entityState(state, A).dim).toBe(0);
    expect(entityState(state, B).dim).toBe(1);
    expect(entityOpacity(state, A)).toBe(1);
    expect(entityOpacity(state, B)).toBeCloseTo(DEEMPHASIS_ALPHA, 10);
  });

  it("zoom_to fits the target box but never past the 2.5 ceiling, centred on it", () => {
    const { compiled, scene } = fixture([action("zoom_to", 0, 27, [A])]);
    const before = resolveSceneState(compiled, scene, 0, FPS).camera;
    expect(before).toEqual({ scale: 1, tx: 0, ty: 0 });
    const after = resolveSceneState(compiled, scene, 27, FPS).camera;
    expect(after.scale).toBe(MAX_ZOOM);
    expect(after.scale * 960 + after.tx).toBeCloseTo(960, 6);
    expect(after.scale * 540 + after.ty).toBeCloseTo(540, 6);
    const mid = resolveSceneState(compiled, scene, 13, FPS).camera;
    expect(mid.scale).toBeGreaterThan(1);
    expect(mid.scale).toBeLessThan(MAX_ZOOM);
  });

  it("an action that has not started contributes nothing", () => {
    const { compiled, scene } = fixture([action("reveal", 10, 22, [A]), action("zoom_to", 10, 37, [A]), action("highlight", 10, 16, [B])], []);
    const state = resolveSceneState(compiled, scene, 5, FPS);
    expect(entityState(state, A).visible).toBe(0);
    expect(entityState(state, B).highlight).toBe(0);
    expect(state.camera).toEqual({ scale: 1, tx: 0, ty: 0 });
    expect(state.annotations).toEqual([]);
  });

  it("seeking to frame N gives the same state whether or not earlier frames were computed", () => {
    const { compiled, scene } = fixture([action("reveal", 0, 12, [A]), action("highlight", 14, 20, [A]), action("isolate", 22, 40, [B]), action("zoom_to", 30, 57, [A])], [B]);
    const direct = resolveSceneState(compiled, scene, 45, FPS);
    for (let f = 0; f < 45; f += 1) resolveSceneState(compiled, scene, f, FPS);
    const afterPlaying = resolveSceneState(compiled, scene, 45, FPS);
    expect(JSON.stringify(afterPlaying)).toBe(JSON.stringify(direct));
  });

  it("a draw target starts undrawn and invisible, then is drawn and visible", () => {
    const { compiled, scene } = fixture([action("draw", 0, 15, [EDGE])]);
    const start = entityState(resolveSceneState(compiled, scene, 0, FPS), EDGE);
    expect(start.draw).toBe(0);
    expect(start.visible).toBe(0);
    const done = entityState(resolveSceneState(compiled, scene, 15, FPS), EDGE);
    expect(done.draw).toBe(1);
    expect(done.visible).toBe(1);
    expect(entityState(resolveSceneState(compiled, scene, 0, FPS), A).draw).toBe(1);
  });

  it("trace reveals its targets in order", () => {
    const { compiled, scene } = fixture([action("trace", 0, 20, [A, B])], []);
    const half = resolveSceneState(compiled, scene, 10, FPS);
    expect(entityState(half, A).trace).toBe(1);
    expect(entityState(half, B).trace).toBe(0);
    expect(entityState(resolveSceneState(compiled, scene, 20, FPS), B).trace).toBe(1);
  });

  it("annotate carries the beat's text and sort its key", () => {
    const beatActions: Beat["actions"] = [
      { action: "annotate", targets: [A], text: "read me", claim_id: null },
      { action: "sort", by: "value_asc" },
    ];
    const { compiled, scene } = fixture([action("annotate", 0, 12, [A], 0), action("sort", 0, 18, [], 1)], [A, B], beatActions);
    const state = resolveSceneState(compiled, scene, 30, FPS);
    expect(state.annotations).toEqual([{ entityId: A, text: "read me", progress: 1 }]);
    expect(state.sort).toEqual({ by: "value_asc", progress: 1 });
  });
});
