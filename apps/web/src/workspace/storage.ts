/**
 * Workspace graphs live in the browser for now (localStorage, strict-parsed on load). Server-side
 * persistence needs a Pydantic contract first; until that exists this module is the only owner of
 * the storage format so the swap stays local.
 */

import {
  applyOps,
  connectOps,
  emptyGraph,
  makeNode,
  newId,
  parseGraph,
  serializeGraph,
  type WorkspaceGraph,
} from "@content-factory/node-graph";
import { workspaceCatalog } from "./catalog";

const STORE_KEY = "cf.workspace.graphs.v1";
const ACTIVE_KEY = "cf.workspace.active.v1";

export interface StoredWorkspace {
  readonly graphs: readonly WorkspaceGraph[];
  readonly activeId: string;
}

/** A starter graph so the first visit shows a working pipeline instead of an empty void. */
export function starterGraph(): WorkspaceGraph {
  let graph = emptyGraph(newId("graph"), "Video from brief");
  const at = (x: number, y: number) => ({ x, y });
  const nodes = {
    brief: makeNode(workspaceCatalog, "input.brief", {
      ...at(-700, -40),
      values: {
        topic: "An islander and their dragon outrun a storm across floating islands.",
        audience: "fantasy short-form viewers",
        quality: "demo",
      },
    }),
    research: makeNode(workspaceCatalog, "research", at(-360, -160)),
    story: makeNode(workspaceCatalog, "plan_story", at(-40, -40)),
    anchor: makeNode(workspaceCatalog, "generate_anchor", {
      ...at(280, -220),
      values: {
        prompt:
          "Premium practical-film photography, post-storm dusk, colossal floating islands, waterfalls into a cloud ocean, physically convincing movement.",
        model: "hidream-o1",
        seed: 535169471117288,
        megapixels: 1.0,
      },
      note: "Anchor look for the whole sequence — keep it painterly, not video-game glossy.",
    }),
    lock: makeNode(workspaceCatalog, "lock_generation", at(660, -240)),
    controls: makeNode(workspaceCatalog, "compile_controls", at(660, -100)),
    keyframes: makeNode(workspaceCatalog, "generate_keyframes", at(960, -180)),
    timeline: makeNode(workspaceCatalog, "compile_timeline", at(280, 120)),
    scenes: makeNode(workspaceCatalog, "render_scenes", at(600, 160)),
    video: makeNode(workspaceCatalog, "compose_video", {
      ...at(1280, -40),
      note: "Final mux — h264 for the demo profile.",
    }),
    qc: makeNode(workspaceCatalog, "qc_deliverable", at(1620, -40)),
    note: makeNode(workspaceCatalog, "utility.note", {
      ...at(-700, 260),
      note: "Drag nodes in from the library on the left.\nDouble-click the canvas to search.\nEvery node takes a note underneath.",
    }),
  };
  for (const node of Object.values(nodes)) {
    graph = applyOps(graph, [{ op: "add_node", node }]).graph;
  }
  const wire = (from: { node: string; slot: string }, to: { node: string; slot: string }) => {
    graph = applyOps(graph, connectOps(graph, from, to)).graph;
  };
  wire({ node: nodes.brief.id, slot: "brief" }, { node: nodes.research.id, slot: "brief" });
  wire({ node: nodes.brief.id, slot: "brief" }, { node: nodes.story.id, slot: "brief" });
  wire({ node: nodes.research.id, slot: "claims" }, { node: nodes.story.id, slot: "claims" });
  wire({ node: nodes.story.id, slot: "story" }, { node: nodes.anchor.id, slot: "story" });
  wire({ node: nodes.anchor.id, slot: "anchor" }, { node: nodes.lock.id, slot: "anchor" });
  wire({ node: nodes.lock.id, slot: "lock" }, { node: nodes.controls.id, slot: "lock" });
  wire({ node: nodes.lock.id, slot: "lock" }, { node: nodes.keyframes.id, slot: "lock" });
  wire({ node: nodes.controls.id, slot: "controls" }, { node: nodes.keyframes.id, slot: "controls" });
  wire({ node: nodes.story.id, slot: "story" }, { node: nodes.timeline.id, slot: "story" });
  wire({ node: nodes.timeline.id, slot: "timeline" }, { node: nodes.scenes.id, slot: "timeline" });
  wire({ node: nodes.keyframes.id, slot: "frames" }, { node: nodes.video.id, slot: "frames" });
  wire({ node: nodes.video.id, slot: "video" }, { node: nodes.qc.id, slot: "deliverable" });
  return graph;
}

function safeParse(value: unknown): WorkspaceGraph | null {
  try {
    return parseGraph(value);
  } catch {
    return null;
  }
}

/** Load everything, dropping entries that no longer parse rather than crashing the page. */
export function loadWorkspace(): StoredWorkspace {
  let graphs: WorkspaceGraph[] = [];
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (raw) {
      const list = JSON.parse(raw) as unknown;
      if (Array.isArray(list)) graphs = list.map(safeParse).filter((g): g is WorkspaceGraph => g !== null);
    }
  } catch {
    graphs = [];
  }
  if (graphs.length === 0) graphs = [starterGraph()];
  let activeId = "";
  try {
    activeId = window.localStorage.getItem(ACTIVE_KEY) ?? "";
  } catch {
    activeId = "";
  }
  if (!graphs.some((g) => g.graph_id === activeId)) activeId = graphs[0]!.graph_id;
  return { graphs, activeId };
}

export function saveGraphs(graphs: readonly WorkspaceGraph[]): void {
  try {
    window.localStorage.setItem(STORE_KEY, `[${graphs.map(serializeGraph).join(",")}]`);
  } catch {
    // Storage full or unavailable: the graph still lives in memory; nothing useful to do here.
  }
}

export function saveActive(graphId: string): void {
  try {
    window.localStorage.setItem(ACTIVE_KEY, graphId);
  } catch {
    // Same as above.
  }
}

export function newUntitledGraph(existing: readonly WorkspaceGraph[]): WorkspaceGraph {
  const names = new Set(existing.map((g) => g.name));
  let index = existing.length + 1;
  let name = `Untitled graph ${index}`;
  while (names.has(name)) name = `Untitled graph ${++index}`;
  return emptyGraph(newId("graph"), name);
}
