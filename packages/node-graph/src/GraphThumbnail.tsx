/**
 * A miniature of a whole graph as a single SVG — nodes as rounded cards with their header strip,
 * links as curves coloured by the source datatype. Template cards use it the way ComfyUI uses
 * workflow preview images, except this one is drawn from the graph itself so it can never lie.
 */

import { useMemo } from "react";
import { slotColor } from "./datatypes";
import type { WorkspaceGraph } from "./graphModel";
import { estimateNodeSize, findSlot, type NodeCatalog } from "./nodeDefs";

export interface GraphThumbnailProps {
  readonly graph: WorkspaceGraph;
  readonly catalog: NodeCatalog;
  /** Rendered size in CSS pixels; the drawing scales to fit either way. */
  readonly width?: number;
  readonly height?: number;
  readonly "aria-label"?: string;
}

const PAD = 40;

export function GraphThumbnail({
  graph,
  catalog,
  width = 320,
  height = 180,
  "aria-label": ariaLabel,
}: GraphThumbnailProps) {
  const drawing = useMemo(() => {
    // Folded groups are drawn as the one node the canvas will draw, and their members are not:
    // the whole point of this picture is that it cannot lie about what opening the graph shows.
    const folded = graph.groups.filter((group) => group.collapsed);
    const hidden = new Set(folded.flatMap((group) => group.members));
    const boxes = graph.nodes
      .filter((node) => !hidden.has(node.id))
      .map((node) => {
        const def = catalog.get(node.type) ?? null;
        const size = estimateNodeSize(node, def);
        return { node, def, x: node.x, y: node.y, ...size, group: false };
      });
    for (const group of folded) {
      boxes.push({
        node: { id: group.id } as (typeof boxes)[number]["node"],
        def: null,
        x: group.x,
        y: group.y,
        // Roughly what GroupNodeView takes: the header, the ports and the step list.
        width: 280,
        height: 30 + 18 + group.members.length * 16 + 60,
        group: true,
      });
    }
    if (boxes.length === 0) return null;
    const minX = Math.min(...boxes.map((b) => b.x)) - PAD;
    const minY = Math.min(...boxes.map((b) => b.y)) - PAD;
    const maxX = Math.max(...boxes.map((b) => b.x + b.width)) + PAD;
    const maxY = Math.max(...boxes.map((b) => b.y + b.height)) + PAD;
    const byId = new Map(boxes.map((b) => [b.node.id, b]));

    const links = graph.links.flatMap((link) => {
      // A link into or out of a folded group is drawn to the group; one inside it is not drawn.
      const fromFolded = graph.groups.find((g) => g.collapsed && g.members.includes(link.from_node));
      const toFolded = graph.groups.find((g) => g.collapsed && g.members.includes(link.to_node));
      if (fromFolded && toFolded && fromFolded.id === toFolded.id) return [];
      const from = byId.get(fromFolded ? fromFolded.id : link.from_node);
      const to = byId.get(toFolded ? toFolded.id : link.to_node);
      if (!from || !to) return [];
      const def = from.def;
      const type = def ? (findSlot(def.outputs, link.from_slot)?.type ?? "ANY") : "ANY";
      const x1 = from.x + from.width;
      const y1 = from.y + from.height / 2;
      const x2 = to.x;
      const y2 = to.y + to.height / 2;
      const bend = Math.max(40, (x2 - x1) / 2);
      return [
        {
          id: link.id,
          color: slotColor(type),
          d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
        },
      ];
    });
    return { boxes, links, viewBox: `${minX} ${minY} ${maxX - minX} ${maxY - minY}` };
  }, [graph, catalog]);

  if (!drawing) return null;
  return (
    <svg
      className="ng-thumb"
      viewBox={drawing.viewBox}
      width={width}
      height={height}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label={ariaLabel ?? `Preview of ${graph.name}`}
    >
      {drawing.links.map((link) => (
        <path key={link.id} d={link.d} fill="none" stroke={link.color} strokeWidth={6} strokeOpacity={0.85} />
      ))}
      {drawing.boxes.map(({ node, def, x, y, width: w, height: h, group }) => (
        <g key={node.id}>
          {/* A folded group wears its stack: two offset plates behind the body, the same mark it
              has on the canvas. */}
          {group && <rect x={x + 10} y={y + 10} width={w} height={h} rx={10} className="ng-thumb__node" opacity={0.35} />}
          {group && <rect x={x + 5} y={y + 5} width={w} height={h} rx={10} className="ng-thumb__node" opacity={0.6} />}
          <rect
            x={x}
            y={y}
            width={w}
            height={h}
            rx={10}
            className="ng-thumb__node"
            data-kind={def?.kind === "note" ? "note" : undefined}
          />
          <rect
            x={x}
            y={y}
            width={w}
            height={26}
            rx={10}
            className="ng-thumb__header"
            data-kind={def?.kind === "note" ? "note" : group ? "group" : undefined}
          />
        </g>
      ))}
    </svg>
  );
}
