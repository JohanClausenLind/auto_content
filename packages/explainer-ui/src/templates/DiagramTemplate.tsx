// Nodes and edges at the positions ELK fixed beforehand; layout coordinates are canvas pixels.
import type { DiagramLayout, DiagramTemplate as DiagramSpec, LayoutEdge, LayoutNode } from "@content-factory/content-schema-ts";
import { measureText } from "@content-factory/content-ui";
import type { ReactElement } from "react";

import { useSceneEnv } from "../context";
import { cameraTransform, entityBoxIndex, regionOf, safeAreaBox } from "../geometry";
import { entityColors, mixHex, tokenHex } from "../palette";
import { entityOpacity, entityState, type SceneState } from "../state";
import { TEXT_STACK, rolePx } from "../text";
import { TOKENS } from "../tokens.gen";
import { polylineLength } from "./chart";
import type { TemplateProps } from "./props";

/** 120 px/s: how fast a flow's dashes travel along an edge at 1080p. */
const FLOW_SPEED_PX_S = 120;

interface DiagramInk {
  primary: string;
  secondary: string;
  emphasis: string;
  strokeStrong: string;
  surface0: string;
  surface2: string;
}

interface DiagramGeometry {
  state: SceneState;
  colors: ReadonlyMap<string, string>;
  scale: number;
  unit: number;
  labelPx: number;
  captionPx: number;
  strokePx: number;
  ink: DiagramInk;
}

function arrowhead(points: LayoutEdge["points"], size: number): { tip: string; shortenBy: number } | null {
  const tip = points[points.length - 1];
  const prev = points[points.length - 2];
  if (!tip || !prev) return null;
  const angle = Math.atan2(tip.y - prev.y, tip.x - prev.x);
  const back = { x: tip.x - Math.cos(angle) * size, y: tip.y - Math.sin(angle) * size };
  const nx = -Math.sin(angle) * (size / 2);
  const ny = Math.cos(angle) * (size / 2);
  return { tip: `${tip.x},${tip.y} ${back.x + nx},${back.y + ny} ${back.x - nx},${back.y - ny}`, shortenBy: size * 0.7 };
}

/** The polyline with its last segment shortened so the stroke ends under the arrowhead. */
function pathOf(points: LayoutEdge["points"], shortenBy: number): string {
  const pts = points.map((p) => ({ x: p.x, y: p.y }));
  const tip = pts[pts.length - 1];
  const prev = pts[pts.length - 2];
  if (tip && prev) {
    const len = Math.hypot(tip.x - prev.x, tip.y - prev.y);
    if (len > shortenBy) {
      tip.x = prev.x + ((tip.x - prev.x) * (len - shortenBy)) / len;
      tip.y = prev.y + ((tip.y - prev.y) * (len - shortenBy)) / len;
    }
  }
  return pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
}

function Edge({ g, edge, label }: { g: DiagramGeometry; edge: LayoutEdge; label: string }): ReactElement {
  const id = edge.entity_id;
  const es = entityState(g.state, id);
  const opacity = entityOpacity(g.state, id);
  const hex = g.colors.get(id) ?? g.ink.strokeStrong;
  const head = arrowhead(edge.points, 3.5 * g.strokePx);
  const d = pathOf(edge.points, head?.shortenBy ?? 0);
  const length = polylineLength(edge.points);
  const width = g.strokePx * (1 + 0.5 * es.highlight);
  const dash = 2 * g.unit;
  const gap = 3 * g.unit;
  const labelW = label === "" ? 0 : Math.ceil(measureText(label, { weight: 500, fontSize: g.captionPx })) + 2 * g.unit;
  const labelH = Math.round(g.captionPx * TOKENS.typography.line_height) + g.unit;
  return (
    <g opacity={opacity}>
      <path d={d} fill="none" stroke={mixHex(hex, g.ink.emphasis, es.highlight)} strokeWidth={width} strokeLinejoin="round" strokeLinecap="round" strokeDasharray={length} strokeDashoffset={length * (1 - es.draw)} />
      {head ? <polygon points={head.tip} fill={mixHex(hex, g.ink.emphasis, es.highlight)} opacity={Math.max(0, Math.min(1, (es.draw - 0.85) / 0.15))} /> : null}
      {es.trace > 0 ? <path d={d} fill="none" stroke={g.ink.emphasis} strokeWidth={width + 2 * g.scale} strokeLinecap="round" strokeDasharray={`${length * es.trace} ${length}`} /> : null}
      {es.flow > 0 ? (
        <path d={d} fill="none" stroke={g.ink.emphasis} strokeWidth={width} strokeLinecap="round" strokeDasharray={`${dash} ${gap}`} strokeDashoffset={-((es.flowSeconds * FLOW_SPEED_PX_S * g.scale) % (dash + gap))} opacity={es.flow} />
      ) : null}
      {label !== "" && edge.label_anchor ? (
        <g>
          <rect x={edge.label_anchor.x - labelW / 2} y={edge.label_anchor.y - labelH / 2} width={labelW} height={labelH} rx={g.unit / 2} fill={g.ink.surface0} />
          <text x={edge.label_anchor.x} y={edge.label_anchor.y} textAnchor="middle" dominantBaseline="central" fontFamily={TEXT_STACK} fontSize={g.captionPx} fontWeight={500} fill={g.ink.secondary}>
            {label}
          </text>
        </g>
      ) : null}
    </g>
  );
}

function Node({ g, node, label, fontPx }: { g: DiagramGeometry; node: LayoutNode; label: string; fontPx: number }): ReactElement {
  const id = node.entity_id;
  const box = node.box;
  const es = entityState(g.state, id);
  const accent = g.colors.get(id) ?? null;
  const axisPx = TOKENS.lines.axis_stroke_px * g.scale;
  return (
    <g opacity={entityOpacity(g.state, id)}>
      <rect x={box.x} y={box.y} width={box.width} height={box.height} rx={1.5 * g.unit} fill={g.ink.surface2} stroke={mixHex(g.ink.strokeStrong, accent ?? g.ink.emphasis, es.highlight)} strokeWidth={axisPx * (1 + es.highlight)} />
      {accent ? <rect x={box.x + g.unit / 2} y={box.y + g.unit} width={g.unit * 0.75} height={Math.max(0, box.height - 2 * g.unit)} rx={g.unit / 4} fill={accent} /> : null}
      {es.trace > 0 ? <rect x={box.x} y={box.y} width={box.width} height={box.height} rx={1.5 * g.unit} fill="none" stroke={g.ink.emphasis} strokeWidth={axisPx * 2} opacity={es.trace} /> : null}
      <svg x={box.x} y={box.y} width={box.width} height={box.height} overflow="hidden">
        <text x={box.width / 2} y={box.height / 2} textAnchor="middle" dominantBaseline="central" fontFamily={TEXT_STACK} fontSize={fontPx} fontWeight={600} fill={g.ink.primary}>
          {label}
        </text>
      </svg>
    </g>
  );
}

export function DiagramTemplate({ compiled, template, state, layout }: TemplateProps<DiagramSpec> & { layout: DiagramLayout }): ReactElement {
  const env = useSceneEnv();
  const { width, height } = env.bundle.timeline;
  const scale = env.scale;
  const region = regionOf(compiled, "plot", "content") ?? safeAreaBox(width, height);
  const boxes = entityBoxIndex(compiled);
  const nodeLabels = new Map(template.nodes.map((n) => [n.entity_id, n.label] as const));
  const edgeLabels = new Map(template.edges.map((e) => [e.entity_id, e.label] as const));
  const labelPx = rolePx("label", scale);
  const g: DiagramGeometry = {
    state,
    colors: entityColors(compiled),
    scale,
    unit: TOKENS.layout.grid.unit * scale,
    labelPx,
    captionPx: rolePx("caption", scale),
    strokePx: TOKENS.lines.data_stroke_px * scale,
    ink: {
      primary: tokenHex("ui.ink.primary"),
      secondary: tokenHex("ui.ink.secondary"),
      emphasis: tokenHex("state.emphasis"),
      strokeStrong: tokenHex("ui.stroke.strong"),
      surface0: tokenHex("ui.surface.0"),
      surface2: tokenHex("ui.surface.2"),
    },
  };
  const clipId = `${compiled.scene_id}-diagram`;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ position: "absolute", left: 0, top: 0 }}>
      <defs>
        <clipPath id={clipId}>
          <rect x={region.x} y={region.y} width={region.width} height={region.height} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`}>
        <g transform={cameraTransform(state.camera)}>
          {layout.edges.map((edge) => (
            <Edge key={edge.entity_id} g={g} edge={edge} label={edgeLabels.get(edge.entity_id) ?? ""} />
          ))}
          {layout.nodes.map((node) => {
            const box = boxes.get(node.entity_id);
            const label = box?.text ?? nodeLabels.get(node.entity_id) ?? env.entities.get(node.entity_id)?.label ?? node.entity_id;
            return <Node key={node.entity_id} g={g} node={box ? { entity_id: node.entity_id, box: box.box } : node} label={label} fontPx={box?.font_px ?? labelPx} />;
          })}
        </g>
      </g>
    </svg>
  );
}
