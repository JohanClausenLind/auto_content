#!/usr/bin/env node
// ELK layouts for every diagram scene of a VisualSpec: --spec <VisualSpec.json> --regions <{scene_id:
// PixelBox}.json> --out <DiagramLayout[].json>. Output coordinates are canvas pixels inside the region.
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { Worker } from "node:worker_threads";

import { INTER_WIDTHS } from "../../../packages/content-ui/src/fonts/inter-widths.ts";
import { TOKENS } from "../../../packages/explainer-ui/src/tokens.gen.ts";
import { UsageError } from "./lib/args.mjs";
import { emit, fail, readBundle, writeAtomically } from "./lib/output.mjs";
import { assertValid, assertValidDef } from "./lib/validate.mjs";
import { writeFileSync } from "node:fs";

const USAGE = "usage: --spec <VisualSpec.json> --regions <regions.json> --out <layouts.json>";
const UNIT = TOKENS.layout.grid.unit;
const LABEL_PX = TOKENS.typography.scale_px_at_1080.label;
const CAPTION_PX = TOKENS.typography.scale_px_at_1080.caption;
const LINE_HEIGHT = TOKENS.typography.line_height;
const here = path.dirname(fileURLToPath(import.meta.url));

function parseLayoutCli() {
  const { values } = parseArgs({
    options: { spec: { type: "string" }, regions: { type: "string" }, out: { type: "string" }, help: { type: "boolean", short: "h" } },
    strict: true,
    allowPositionals: false,
  });
  if (values.help || !values.spec || !values.regions || !values.out) throw new UsageError(USAGE);
  return { spec: values.spec, regions: values.regions, out: path.resolve(values.out) };
}

// The advance sum measureText() in @content-factory/content-ui performs; Node cannot load that
// module directly (extensionless relative imports), so the width table is read here instead.
function measure(text, weight, fontSize) {
  const table = INTER_WIDTHS[weight];
  let units = 0;
  for (const ch of text) units += table[ch] ?? table.n;
  return (units / 1000) * fontSize;
}

const ceilUnit = (v) => Math.ceil(v / UNIT) * UNIT;
const round3 = (v) => Math.round(v * 1000) / 1000;

function elkGraph(template) {
  const direction = template.direction === "TB" ? "DOWN" : "RIGHT";
  return {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": direction,
      "elk.edgeRouting": "ORTHOGONAL",
      "elk.randomSeed": "1",
      "elk.padding": "[top=0,left=0,bottom=0,right=0]",
      "elk.spacing.nodeNode": String(6 * UNIT),
      "elk.spacing.edgeNode": String(4 * UNIT),
      "elk.spacing.edgeLabel": String(UNIT),
      "elk.layered.spacing.nodeNodeBetweenLayers": String(15 * UNIT),
      "elk.layered.spacing.edgeNodeBetweenLayers": String(6 * UNIT),
    },
    children: template.nodes.map((node) => ({
      id: node.entity_id,
      width: ceilUnit(measure(node.label, 600, LABEL_PX) + 6 * UNIT),
      height: ceilUnit(LABEL_PX * LINE_HEIGHT + 4 * UNIT),
      labels: [{ text: node.label }],
    })),
    edges: template.edges.map((edge) => ({
      id: edge.entity_id,
      sources: [edge.source_entity_id],
      targets: [edge.target_entity_id],
      labels: edge.label === "" ? [] : [{ text: edge.label, width: Math.ceil(measure(edge.label, 500, CAPTION_PX) + 2 * UNIT), height: Math.ceil(CAPTION_PX * LINE_HEIGHT + UNIT) }],
    })),
  };
}

/** Scale the ELK result down to the region when needed (never up) and centre it there. */
function toLayout(sceneId, laid, region) {
  const gw = Math.max(1, laid.width ?? 1);
  const gh = Math.max(1, laid.height ?? 1);
  const s = Math.min(1, region.width / gw, region.height / gh);
  const ox = region.x + (region.width - gw * s) / 2;
  const oy = region.y + (region.height - gh * s) / 2;
  const nodes = (laid.children ?? []).map((node) => ({
    entity_id: node.id,
    box: { x: Math.round(ox + node.x * s), y: Math.round(oy + node.y * s), width: Math.max(1, Math.round(node.width * s)), height: Math.max(1, Math.round(node.height * s)) },
  }));
  const edges = (laid.edges ?? []).map((edge) => {
    const section = edge.sections?.[0];
    if (!section) throw new Error(`ELK returned no route for edge ${edge.id}`);
    const points = [section.startPoint, ...(section.bendPoints ?? []), section.endPoint].map((p) => ({ x: round3(ox + p.x * s), y: round3(oy + p.y * s) }));
    const label = edge.labels?.[0];
    const label_anchor = label && label.x !== undefined ? { x: round3(ox + (label.x + label.width / 2) * s), y: round3(oy + (label.y + label.height / 2) * s) } : null;
    return { entity_id: edge.id, points, label_anchor };
  });
  return { scene_id: sceneId, width: Math.max(1, Math.round(gw * s)), height: Math.max(1, Math.round(gh * s)), nodes, edges };
}

function layoutInWorker(worker, id, graph) {
  return new Promise((resolve, reject) => {
    const onMessage = (msg) => {
      if (msg.id !== id) return;
      worker.off("message", onMessage);
      if (msg.ok) resolve(msg.graph);
      else reject(new Error(`ELK failed for ${id}: ${msg.error}`));
    };
    worker.on("message", onMessage);
    worker.postMessage({ id, graph, layoutOptions: graph.layoutOptions });
  });
}

try {
  const args = parseLayoutCli();
  const spec = assertValid("VisualSpec", readBundle(args.spec));
  const regions = readBundle(args.regions);
  const diagrams = spec.scenes.filter((scene) => scene.template.template === "diagram");
  for (const scene of diagrams) {
    if (!regions[scene.scene_id]) throw new UsageError(`--regions has no region for diagram scene ${scene.scene_id}`);
    assertValidDef("ExplainerRenderBundle", "PixelBox", regions[scene.scene_id]);
  }
  const worker = new Worker(path.join(here, "lib", "elk-worker.mjs"));
  worker.on("error", (err) => fail(err));
  const layouts = [];
  try {
    for (const scene of diagrams) {
      const laid = await layoutInWorker(worker, scene.scene_id, elkGraph(scene.template));
      layouts.push(assertValidDef("ExplainerRenderBundle", "DiagramLayout", toLayout(scene.scene_id, laid, regions[scene.scene_id])));
    }
  } finally {
    await worker.terminate();
  }
  await writeAtomically(args.out, async (tmp) => writeFileSync(tmp, `${JSON.stringify(layouts, null, 2)}\n`));
  emit({ out: args.out, layouts: layouts.map((l) => ({ scene_id: l.scene_id, width: l.width, height: l.height, nodes: l.nodes.length, edges: l.edges.length })) });
} catch (err) {
  fail(err);
}
