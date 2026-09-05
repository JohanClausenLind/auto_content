import type { DiagramEdge, DiagramNode, FlowDiagramScene as Spec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { enter, motionFrames, progress } from "../motion";
import { Lines, SceneFrame, useFittedText, useSceneGeometry, type SceneProps } from "./common";

/**
 * Longest-path layers: a node sits one step after the deepest thing that feeds it.
 *
 * Pure, total and cycle-safe. Relaxing every edge `n` times is Bellman-Ford's bound and settles a
 * DAG exactly; a cycle — which the contract does not forbid, because `flow_diagram` and
 * `relationship_diagram` share one node/edge shape — stops at the same bound with every node
 * placed somewhere rather than looping forever. Order inside a layer is declaration order, so the
 * same plan always draws the same picture: this renderer's determinism rule reaches layout too.
 *
 * Edges naming an unknown node are ignored here and drawn by nobody, which is the honest reading:
 * an edge to a node that does not exist is not a shape, and inventing a node for it would put a
 * box on screen that the plan never asked for.
 */
export function flowLayers(nodes: readonly DiagramNode[], edges: readonly DiagramEdge[]): string[][] {
  const known = new Set(nodes.map((n) => n.node_id));
  const real = edges.filter((e) => known.has(e.from_id) && known.has(e.to_id) && e.from_id !== e.to_id);
  const depth = new Map<string, number>(nodes.map((n) => [n.node_id, 0]));
  for (let pass = 0; pass < nodes.length; pass += 1) {
    let moved = false;
    for (const e of real) {
      const next = (depth.get(e.from_id) ?? 0) + 1;
      if (next > (depth.get(e.to_id) ?? 0) && next < nodes.length) {
        depth.set(e.to_id, next);
        moved = true;
      }
    }
    if (!moved) break;
  }
  const deepest = Math.max(0, ...depth.values());
  const layers: string[][] = Array.from({ length: deepest + 1 }, () => []);
  for (const n of nodes) layers[depth.get(n.node_id) ?? 0]!.push(n.node_id);
  return layers;
}

export interface NodeBox {
  node_id: string;
  left: number;
  top: number;
  width: number;
  height: number;
  layer: number;
}

/** Boxes for every node inside a plot box; `axis` is the direction the flow runs. */
/**
 * How much wider the gap between layers is than the seam between siblings.
 *
 * An arrow is the only thing that carries causation in this scene, and an arrow needs length to be
 * one: with a single gap for both axes the layer-to-layer arrows came out as flat stubs a few
 * pixels long, reading as boxes that happen to be stacked rather than boxes that lead to each
 * other.
 */
export const FLOW_GAP_RATIO = 3;

export function flowBoxes(
  layers: readonly (readonly string[])[],
  plot: { width: number; height: number },
  axis: "horizontal" | "vertical",
  gap: number,
): NodeBox[] {
  const boxes: NodeBox[] = [];
  const flowSpan = axis === "horizontal" ? plot.width : plot.height;
  const crossSpan = axis === "horizontal" ? plot.height : plot.width;
  const lane = flowSpan / Math.max(1, layers.length);
  // Never eat more than half a lane, so a six-layer flow still has boxes to put labels in.
  const flowGap = Math.min(gap * FLOW_GAP_RATIO, lane / 2);
  const boxFlow = Math.max(1, lane - flowGap);
  layers.forEach((layer, i) => {
    const slot = crossSpan / Math.max(1, layer.length);
    const boxCross = Math.max(1, slot - gap);
    layer.forEach((node_id, j) => {
      const flowStart = lane * i + (lane - boxFlow) / 2;
      const crossStart = slot * j + (slot - boxCross) / 2;
      boxes.push(
        axis === "horizontal"
          ? { node_id, left: flowStart, top: crossStart, width: boxFlow, height: boxCross, layer: i }
          : { node_id, left: crossStart, top: flowStart, width: boxCross, height: boxFlow, layer: i },
      );
    });
  });
  return boxes;
}

/** A cubic from one box to another, leaving and entering along the flow axis. */
export function edgePath(from: NodeBox, to: NodeBox, axis: "horizontal" | "vertical"): string {
  if (axis === "horizontal") {
    const x1 = from.left + from.width;
    const y1 = from.top + from.height / 2;
    const x2 = to.left;
    const y2 = to.top + to.height / 2;
    const bend = Math.max(12, (x2 - x1) / 2);
    return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
  }
  const x1 = from.left + from.width / 2;
  const y1 = from.top + from.height;
  const x2 = to.left + to.width / 2;
  const y2 = to.top;
  const bend = Math.max(12, (y2 - y1) / 2);
  return `M ${x1} ${y1} C ${x1} ${y1 + bend}, ${x2} ${y2 - bend}, ${x2} ${y2}`;
}

/**
 * Frames per reveal step, paced off the scene's own length rather than a motion token.
 *
 * The first version paced it off `theme.motion.duration.countUp`, and on a 48-frame beat that put
 * the last arrow's reveal past the end of the scene: the final link in the chain was drawn at
 * 55 % with no arrowhead and simply stopped in mid-air. A diagram's reveal has to finish inside
 * the beat that shows it, so the beat's duration is what sets the cadence — the same reasoning as
 * `bulletRevealFrame`. `steps` is layers plus the arrows after them: one more than the layers.
 */
export function flowRevealStep(layers: number, durationInFrames: number, introFrames: number, tailFrames: number): number {
  const usable = durationInFrames - introFrames - tailFrames;
  return Math.max(1, Math.floor(usable / Math.max(1, layers + 1)));
}

function NodeCard({ box, label, delay, terminal }: { box: NodeBox; label: string; delay: number; terminal: boolean }): ReactElement {
  const frame = useCurrentFrame();
  const { theme, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const pad = Math.round(theme.space[4]! * scale);
  const block = useFittedText(
    label,
    "body",
    Math.max(1, box.width - pad * 2),
    Math.max(1, box.height - pad * 2),
    3,
    terminal ? "surface" : "paper",
  );
  return (
    <div
      data-flow-node={box.node_id}
      style={{
        position: "absolute",
        left: box.left,
        top: box.top,
        width: box.width,
        height: box.height,
        boxSizing: "border-box",
        padding: pad,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        textAlign: "center",
        background: terminal ? theme.color.surface : theme.color.paper,
        border: `${Math.max(2, Math.round(3 * scale))}px solid ${terminal ? theme.color.accent : theme.color.rule}`,
        borderRadius: theme.radius.md * scale,
        ...enter(frame, delay, f.base, theme, 10 * scale),
      }}
    >
      <Lines block={block} align="center" />
    </div>
  );
}

/**
 * A mechanism: boxes in longest-path layers with the arrows between them drawn in.
 *
 * `flow_diagram` is how an explainer says "this causes this causes this", and the arc's
 * `mechanism` section is built around it — so with no component, the one scene kind that carries
 * causation rendered as a grey placeholder card. Landscape runs the flow left to right; portrait
 * runs it top to bottom, because a four-layer flow across a 1080-wide frame gives each box 250 px.
 *
 * Nodes appear layer by layer and each arrow draws itself over the gap it spans, so a viewer reads
 * the chain in the order it happens rather than being shown a finished graph. The reveal is
 * `progress(frame, …)`, not a CSS transition: the frame stays the only clock.
 */
export function FlowDiagramScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps, portrait, align, durationInFrames } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const title = useFittedText(scene.title.text, portrait ? "headline" : "label", safe.width, Math.round(safe.height * 0.16), 2, "paper");

  const layers = flowLayers(scene.nodes, scene.edges);
  const axis = portrait ? "vertical" : "horizontal";
  const plot = { width: safe.width, height: Math.round(safe.height * (portrait ? 0.72 : 0.7)) };
  const gap = Math.round(theme.space[5]! * scale);
  const boxes = flowBoxes(layers, plot, axis, gap);
  const byId = new Map(boxes.map((b) => [b.node_id, b]));
  const labelOf = new Map(scene.nodes.map((n) => [n.node_id, n.label.text]));

  // One reveal step per layer plus one for the last arrows, spread over the beat.
  const perLayer = flowRevealStep(layers.length, durationInFrames, f.base, f.base);
  const stroke = Math.max(2, Math.round(3 * scale));
  const head = Math.max(6, Math.round(9 * scale));

  return (
    <SceneFrame testId="flow_diagram" justify={portrait ? "center" : "start"}>
      <Lines block={title} align={align} style={enter(frame, 0, f.base, theme, 10 * scale)} />
      <div style={{ position: "relative", ...plot, marginTop: Math.round(theme.space[6]! * scale) }}>
        <svg width={plot.width} height={plot.height} viewBox={`0 0 ${plot.width} ${plot.height}`} style={{ position: "absolute", left: 0, top: 0 }} aria-hidden="true">
          <defs>
            <marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth={head} markerHeight={head} orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={theme.color.muted} />
            </marker>
          </defs>
          {scene.edges.map((edge, i) => {
            const from = byId.get(edge.from_id);
            const to = byId.get(edge.to_id);
            if (!from || !to) return null;
            // An arrow draws once both of its boxes are on screen.
            const t = progress(frame, perLayer * (Math.max(from.layer, to.layer) + 1), perLayer, theme.motion.easing.standard);
            if (t <= 0) return null;
            return (
              <path
                key={i}
                data-flow-edge={`${edge.from_id}->${edge.to_id}`}
                d={edgePath(from, to, axis)}
                fill="none"
                stroke={theme.color.muted}
                strokeWidth={stroke}
                strokeLinecap="round"
                pathLength={1}
                strokeDasharray={1}
                strokeDashoffset={1 - t}
                markerEnd={t > 0.98 ? "url(#flow-arrow)" : undefined}
              />
            );
          })}
        </svg>
        {boxes.map((box) => (
          <NodeCard
            key={box.node_id}
            box={box}
            label={labelOf.get(box.node_id) ?? box.node_id}
            delay={perLayer * box.layer}
            terminal={box.layer === layers.length - 1}
          />
        ))}
      </div>
    </SceneFrame>
  );
}
