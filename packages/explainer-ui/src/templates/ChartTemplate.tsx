// Bar, stacked bar, line, area, scatter and slope charts as hand-drawn SVG; d3 only computes.
import type { ChartTemplate as ChartSpec, EvidenceDataset } from "@content-factory/content-schema-ts";
import { measureText } from "@content-factory/content-ui";
import { area as d3Area, line as d3Line, range, scaleBand, scaleLinear } from "d3";
import type { ReactElement } from "react";

import { useSceneEnv } from "../context";
import { cameraTransform, clamp01, regionOf, safeAreaBox, type PixelBox } from "../geometry";
import { categoricalTokenId, entityColors, entityHex, tokenHex } from "../palette";
import { entityDimFactor, entityOpacity, entityState, type SceneState } from "../state";
import { DISPLAY_STACK, TEXT_STACK, lineHeightPx, rolePx } from "../text";
import { TOKENS } from "../tokens.gen";
import {
  categorySlot,
  chartCategories,
  filterMask,
  formatValue,
  lineSegments,
  niceTicks,
  numberOrNull,
  pointScale,
  sortOrder,
  stackCategory,
  stackedTotals,
  valueDomain,
  valueLabelSide,
  valueScale,
  type ChartCategory,
  type LabelSide,
  type Slot,
  type ValueDomain,
} from "./chart";
import type { TemplateProps } from "./props";

interface Pt {
  x: number;
  y: number;
}

/** Where a series' mark tops out in one category, for brackets and annotations. */
interface MarkPoint extends Pt {
  value: number;
}

/** Marks draw inside the plot clip; every text label draws in the unclipped layer above them. */
type Layer = "marks" | "labels";

type Anchor = "start" | "middle" | "end";

interface ChartGeometry {
  template: ChartSpec;
  state: SceneState;
  categories: ChartCategory[];
  slots: Slot[];
  /** The data frame: the compiled plot region, or an inset of it when the scene has no axis regions. */
  frame: PixelBox;
  y: (v: number) => number;
  bandLeft: (slot: number) => number;
  bandWidth: number;
  pointX: (slot: number) => number;
  xLinear: ((v: number) => number) | null;
  seriesHex: (i: number) => string;
  scale: number;
  unit: number;
  labelPx: number;
  /** Right edge of the y tick labels and baseline of the x tick labels, inside the axis regions. */
  tickLabelX: number;
  xLabelY: number;
  ink: { primary: string; secondary: string; muted: string; emphasis: string; surface2: string; strokeSoft: string };
}

const N_LABEL_MAX = 8;

function chartTitle(dataset: EvidenceDataset, template: ChartSpec): string {
  if (template.title !== "") return template.title;
  if (dataset.title !== "") return dataset.title;
  return `${template.y.title || template.y.field} by ${template.x.title || template.x.field}`;
}

function markPoint(g: ChartGeometry, series: number, category: number): MarkPoint | null {
  const cat = g.categories[category];
  const slot = g.slots[category];
  const v = cat?.values[series];
  if (!cat || !slot || v === null || v === undefined) return null;
  const kind = g.template.chart_kind;
  if (kind === "bar") {
    const k = g.template.series.length;
    const sub = g.bandWidth / k;
    return { x: g.bandLeft(slot.position) + sub * series + sub / 2, y: g.y(Math.max(0, v)), value: v };
  }
  if (kind === "stacked_bar") {
    const seg = stackCategory(cat.values).find((s) => s.series === series);
    return seg ? { x: g.bandLeft(slot.position) + g.bandWidth / 2, y: g.y(seg.y1), value: v } : null;
  }
  if (kind === "scatter") {
    const xv = numberOrNull(cat.x);
    return xv === null || g.xLinear === null ? null : { x: g.xLinear(xv), y: g.y(v), value: v };
  }
  if (kind === "slope") return { x: slopeX(g, category === 0 ? 0 : 1), y: g.y(v), value: v };
  return { x: g.pointX(slot.position), y: g.y(v), value: v };
}

/** Slope charts use two fixed columns, 15 % in from either side of the plot. */
function slopeX(g: ChartGeometry, end: 0 | 1): number {
  return g.frame.x + g.frame.width * (end === 0 ? 0.15 : 0.85);
}

function lastPresent(g: ChartGeometry, series: number): number {
  for (let i = g.categories.length - 1; i >= 0; i -= 1) if (g.categories[i]?.values[series] !== null) return i;
  return -1;
}

function seriesIndex(template: ChartSpec, entityId: string): number {
  return template.series.findIndex((s) => s.entity_id === entityId);
}

/** Baseline and anchor of a label `gap` px off a mark at (x, y) on `side`; 0.75 em is Inter's cap height. */
function labelAt(g: ChartGeometry, x: number, y: number, side: LabelSide, gap: number): { x: number; y: number; anchor: Anchor } {
  if (side === "left") return { x: x - gap, y: y + 0.35 * g.labelPx, anchor: "end" };
  if (side === "below") return { x, y: y + gap + 0.75 * g.labelPx, anchor: "middle" };
  return { x, y: y - gap, anchor: "middle" };
}

function ValueLabel({ g, x, y, text, opacity, anchor = "middle" }: { g: ChartGeometry; x: number; y: number; text: string; opacity: number; anchor?: Anchor }): ReactElement {
  return (
    <text x={x} y={y} textAnchor={anchor} fontFamily={TEXT_STACK} fontSize={g.labelPx} fontWeight={600} fill={g.ink.primary} opacity={opacity}>
      {text}
    </text>
  );
}

function Bars({ g, layer }: { g: ChartGeometry; layer: Layer }): ReactElement {
  const k = g.template.series.length;
  const sub = g.bandWidth / k;
  const zero = g.y(0);
  const unit = g.template.y.unit;
  return (
    <g>
      {g.categories.map((cat, i) => {
        const slot = g.slots[i];
        if (!slot) return null;
        return cat.values.map((v, si) => {
          const id = g.template.series[si]?.entity_id;
          if (v === null || id === undefined) return null;
          const es = entityState(g.state, id);
          const grown = Math.abs(g.y(v) - zero) * es.visible;
          const top = v >= 0 ? zero - grown : zero;
          const x = g.bandLeft(slot.position) + sub * si;
          const dim = entityDimFactor(g.state, id) * slot.alpha;
          const key = `${cat.key}-${si}`;
          if (layer === "labels") return <ValueLabel key={key} g={g} x={x + sub / 2} y={v >= 0 ? top - g.unit / 2 : top + grown + g.labelPx} text={formatValue(v, unit)} opacity={es.visible * dim} />;
          return <rect key={key} x={x + g.unit / 4} y={top} width={Math.max(0, sub - g.unit / 2)} height={grown} fill={g.seriesHex(si)} opacity={dim} stroke={g.ink.emphasis} strokeWidth={3 * g.scale * es.highlight} />;
        });
      })}
    </g>
  );
}

function StackedBars({ g, layer }: { g: ChartGeometry; layer: Layer }): ReactElement {
  const unit = g.template.y.unit;
  return (
    <g>
      {g.categories.map((cat, i) => {
        const slot = g.slots[i];
        if (!slot) return null;
        const shown = cat.values.map((v, si) => {
          const id = g.template.series[si]?.entity_id;
          return v === null || id === undefined ? null : v * entityState(g.state, id).visible;
        });
        const segments = stackCategory(shown);
        const x = g.bandLeft(slot.position);
        const total = segments.reduce((sum, s) => sum + s.value, 0);
        const fullTotal = stackedTotals([cat])[0] ?? 0;
        const allVisible = Math.min(1, ...cat.values.map((v, si) => (v === null ? 1 : entityState(g.state, g.template.series[si]?.entity_id ?? "").visible)));
        const top = Math.min(g.y(0), ...segments.map((s) => g.y(s.y1)));
        if (layer === "labels") {
          return segments.length > 0 ? <ValueLabel key={cat.key} g={g} x={x + g.bandWidth / 2} y={top - g.unit / 2} text={formatValue(allVisible >= 1 ? fullTotal : total, unit)} opacity={allVisible * slot.alpha} /> : null;
        }
        return (
          <g key={cat.key}>
            {segments.map((seg) => {
              const id = g.template.series[seg.series]?.entity_id ?? "";
              const es = entityState(g.state, id);
              const y1 = g.y(seg.y1);
              const y0 = g.y(seg.y0);
              return (
                <rect
                  key={seg.series}
                  x={x}
                  y={Math.min(y0, y1)}
                  width={g.bandWidth}
                  height={Math.abs(y0 - y1)}
                  fill={g.seriesHex(seg.series)}
                  opacity={entityDimFactor(g.state, id) * slot.alpha}
                  stroke={g.ink.emphasis}
                  strokeWidth={3 * g.scale * es.highlight}
                />
              );
            })}
          </g>
        );
      })}
    </g>
  );
}

function Lines({ g, filled, clipPrefix, layer }: { g: ChartGeometry; filled: boolean; clipPrefix: string; layer: Layer }): ReactElement {
  const zero = Math.min(g.frame.y + g.frame.height, Math.max(g.frame.y, g.y(0)));
  const path = d3Line<Pt>()
    .x((d) => d.x)
    .y((d) => d.y);
  const fill = d3Area<Pt>()
    .x((d) => d.x)
    .y0(zero)
    .y1((d) => d.y);
  const strokePx = TOKENS.lines.data_stroke_px * g.scale;
  const unit = g.template.y.unit;
  const labelH = lineHeightPx(g.labelPx);
  return (
    <g>
      {g.template.series.map((binding, si) => {
        const es = entityState(g.state, binding.entity_id);
        const opacity = entityOpacity(g.state, binding.entity_id);
        const points = g.categories.map((cat, i) => {
          const v = cat.values[si];
          const slot = g.slots[i];
          return v === null || v === undefined || !slot ? null : { x: g.pointX(slot.position), y: g.y(v), value: v, alpha: slot.alpha };
        });
        // The reveal clip grows from the left; a label fades in as the clip's right edge passes its mark.
        const revealX = g.frame.x - g.unit;
        const revealW = (g.frame.width + 2 * g.unit) * es.visible;
        if (layer === "labels") {
          if (g.categories.length > N_LABEL_MAX) return null;
          const last = lastPresent(g, si);
          return (
            <g key={binding.entity_id} opacity={opacity}>
              {points.map((p, j) => {
                if (p === null) return null;
                const at = labelAt(g, p.x, p.y, valueLabelSide(p.y, g.frame, labelH, j === last), 1.5 * g.unit);
                return <ValueLabel key={j} g={g} x={at.x} y={at.y} anchor={at.anchor} text={formatValue(p.value, unit)} opacity={p.alpha * clamp01((revealX + revealW - p.x) / g.unit)} />;
              })}
            </g>
          );
        }
        const clipId = `${clipPrefix}-reveal-${si}`;
        const width = strokePx * (1 + 0.5 * es.highlight);
        return (
          <g key={binding.entity_id} opacity={opacity}>
            <clipPath id={clipId}>
              <rect x={revealX} y={g.frame.y - 3 * g.unit} width={revealW} height={g.frame.height + 6 * g.unit} />
            </clipPath>
            <g clipPath={`url(#${clipId})`}>
              {lineSegments(points).map((segment, k) => (
                <g key={k}>
                  {filled ? <path d={fill(segment) ?? ""} fill={g.seriesHex(si)} opacity={0.25} /> : null}
                  <path d={path(segment) ?? ""} fill="none" stroke={g.seriesHex(si)} strokeWidth={width} strokeLinejoin="round" strokeLinecap="round" />
                  {segment.map((p, j) => (
                    <circle key={j} cx={p.x} cy={p.y} r={strokePx * (1 + 0.5 * es.highlight)} fill={g.seriesHex(si)} opacity={p.alpha} />
                  ))}
                </g>
              ))}
            </g>
          </g>
        );
      })}
    </g>
  );
}

function Scatter({ g }: { g: ChartGeometry }): ReactElement {
  const r = TOKENS.lines.data_stroke_px * 2 * g.scale;
  return (
    <g>
      {g.template.series.map((binding, si) => {
        const es = entityState(g.state, binding.entity_id);
        const opacity = entityOpacity(g.state, binding.entity_id);
        return (
          <g key={binding.entity_id} opacity={opacity}>
            {g.categories.map((cat, i) => {
              const p = markPoint(g, si, i);
              const slot = g.slots[i];
              if (!p || !slot) return null;
              return <circle key={cat.key} cx={p.x} cy={p.y} r={r * (1 + 0.5 * es.highlight)} fill={g.seriesHex(si)} opacity={slot.alpha} stroke={g.ink.emphasis} strokeWidth={2 * g.scale * es.highlight} />;
            })}
          </g>
        );
      })}
    </g>
  );
}

function Slope({ g, clipPrefix, layer }: { g: ChartGeometry; clipPrefix: string; layer: Layer }): ReactElement {
  const env = useSceneEnv();
  const strokePx = TOKENS.lines.data_stroke_px * g.scale;
  const last = g.categories.length - 1;
  const unit = g.template.y.unit;
  const xl = slopeX(g, 0);
  const xr = slopeX(g, 1);
  return (
    <g>
      {g.template.series.map((binding, si) => {
        const a = g.categories[0]?.values[si] ?? null;
        const b = g.categories[last]?.values[si] ?? null;
        if (a === null || b === null) return null;
        const es = entityState(g.state, binding.entity_id);
        const opacity = entityOpacity(g.state, binding.entity_id);
        const revealX = xl - 2 * g.unit;
        const revealW = (xr - xl + 4 * g.unit) * es.visible;
        if (layer === "labels") {
          const label = env.entities.get(binding.entity_id);
          return (
            <g key={binding.entity_id} opacity={opacity}>
              <ValueLabel g={g} x={xl - 1.5 * g.unit} y={g.y(a) + g.labelPx * 0.35} text={formatValue(a, unit)} opacity={clamp01((revealX + revealW - xl) / g.unit)} anchor="end" />
              <ValueLabel g={g} x={xr + 1.5 * g.unit} y={g.y(b) + g.labelPx * 0.35} text={`${formatValue(b, unit)}  ${label?.short_label || label?.label || ""}`} opacity={clamp01((revealX + revealW - xr) / g.unit)} anchor="start" />
            </g>
          );
        }
        const clipId = `${clipPrefix}-slope-${si}`;
        return (
          <g key={binding.entity_id} opacity={opacity}>
            <clipPath id={clipId}>
              <rect x={revealX} y={g.frame.y - 3 * g.unit} width={revealW} height={g.frame.height + 6 * g.unit} />
            </clipPath>
            <g clipPath={`url(#${clipId})`}>
              <line x1={xl} y1={g.y(a)} x2={xr} y2={g.y(b)} stroke={g.seriesHex(si)} strokeWidth={strokePx * (1 + 0.5 * es.highlight)} strokeLinecap="round" />
              <circle cx={xl} cy={g.y(a)} r={strokePx} fill={g.seriesHex(si)} />
              <circle cx={xr} cy={g.y(b)} r={strokePx} fill={g.seriesHex(si)} />
            </g>
          </g>
        );
      })}
    </g>
  );
}

function Axes({ g, domain }: { g: ChartGeometry; domain: { min: number; max: number; broken: boolean } }): ReactElement {
  const axisPx = TOKENS.lines.axis_stroke_px * g.scale;
  const gridPx = TOKENS.lines.grid_stroke_px * g.scale;
  const ticks = niceTicks(domain);
  const { x: left, y: top, width, height } = g.frame;
  const bottom = top + height;
  const baseline = domain.min <= 0 && domain.max >= 0 ? g.y(0) : bottom;
  const kind = g.template.chart_kind;
  const unit = g.template.y.unit;
  const captionPx = rolePx("caption", g.scale);
  const xLabels =
    kind === "slope"
      ? [
          { x: slopeX(g, 0), text: g.categories[0]?.label ?? "", alpha: 1 },
          { x: slopeX(g, 1), text: g.categories[g.categories.length - 1]?.label ?? "", alpha: 1 },
        ]
      : kind === "scatter" && g.xLinear !== null
        ? niceTicks(valueDomain(g.categories.map((c) => numberOrNull(c.x)), false)).map((t) => ({ x: g.xLinear?.(t) ?? 0, text: formatValue(t, g.template.x.unit), alpha: 1 }))
        : g.categories.map((c, i) => {
            const slot = g.slots[i] ?? { position: i, alpha: 1 };
            const x = kind === "bar" || kind === "stacked_bar" ? g.bandLeft(slot.position) + g.bandWidth / 2 : g.pointX(slot.position);
            return { x, text: c.label, alpha: slot.alpha };
          });
  return (
    <g fontFamily={TEXT_STACK} fontSize={g.labelPx} fill={g.ink.secondary}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={left} x2={left + width} y1={g.y(t)} y2={g.y(t)} stroke={g.ink.strokeSoft} strokeWidth={gridPx} />
          <line x1={left - g.unit} x2={left} y1={g.y(t)} y2={g.y(t)} stroke={g.ink.muted} strokeWidth={axisPx} />
          <text x={g.tickLabelX} y={g.y(t)} textAnchor="end" dominantBaseline="central">
            {formatValue(t, unit)}
          </text>
        </g>
      ))}
      <line x1={left} x2={left} y1={top} y2={domain.broken ? bottom - 3 * g.unit : bottom} stroke={g.ink.muted} strokeWidth={axisPx} />
      {domain.broken ? (
        <g>
          <polyline
            points={`${left},${bottom - 3 * g.unit} ${left - g.unit},${bottom - 2.25 * g.unit} ${left + g.unit},${bottom - 1.5 * g.unit} ${left},${bottom - 0.75 * g.unit} ${left},${bottom}`}
            fill="none"
            stroke={g.ink.muted}
            strokeWidth={axisPx}
          />
          <text x={left} y={g.xLabelY + lineHeightPx(captionPx)} fontSize={captionPx} textAnchor="start">
            {`axis starts at ${formatValue(domain.min, unit)}`}
          </text>
        </g>
      ) : null}
      <line x1={left} x2={left + width} y1={baseline} y2={baseline} stroke={g.ink.muted} strokeWidth={axisPx} />
      {xLabels.map((l, i) => (
        <text key={i} x={l.x} y={g.xLabelY} textAnchor="middle" opacity={l.alpha}>
          {l.text}
        </text>
      ))}
    </g>
  );
}

function Compare({ g }: { g: ChartGeometry }): ReactElement | null {
  const pair = g.state.compare;
  if (!pair) return null;
  const [a, b] = [seriesIndex(g.template, pair[0]), seriesIndex(g.template, pair[1])];
  if (a < 0 || b < 0) return null;
  const category = Math.min(lastPresent(g, a), lastPresent(g, b));
  const pa = markPoint(g, a, Math.max(0, category));
  const pb = markPoint(g, b, Math.max(0, category));
  if (!pa || !pb) return null;
  const top = Math.min(pa.y, pb.y) - 4 * g.unit;
  const unit = g.template.y.unit;
  const axisPx = TOKENS.lines.axis_stroke_px * g.scale;
  return (
    <g opacity={g.state.compareProgress}>
      <path d={`M ${pa.x} ${pa.y - g.unit} V ${top} H ${pb.x} V ${pb.y - g.unit}`} fill="none" stroke={g.ink.emphasis} strokeWidth={axisPx} />
      <ValueLabel g={g} x={pa.x} y={top - g.unit} text={formatValue(pa.value, unit)} opacity={1} anchor={pa.x <= pb.x ? "end" : "start"} />
      <ValueLabel g={g} x={pb.x} y={top - g.unit} text={formatValue(pb.value, unit)} opacity={1} anchor={pa.x <= pb.x ? "start" : "end"} />
    </g>
  );
}

function Annotations({ g }: { g: ChartGeometry }): ReactElement {
  const padX = g.unit;
  const labelH = lineHeightPx(g.labelPx);
  const chipH = labelH + g.unit;
  // Marks that carry a value label get the chip just above that label: label + gaps stay under the
  // 48 px label distance (35 + 4 + 8 at 1080p), and the leader no longer crosses the value.
  const overValueLabel = g.template.chart_kind !== "scatter";
  const anchorGap = overValueLabel ? labelH + g.unit / 2 : g.unit / 2;
  const gap = overValueLabel ? g.unit : Math.min(TOKENS.layout.label_distance_max_px * g.scale, 3 * g.unit);
  return (
    <g>
      {g.state.annotations.map((note, i) => {
        const si = seriesIndex(g.template, note.entityId);
        const p = si < 0 ? null : markPoint(g, si, lastPresent(g, si));
        if (!p) return null;
        const w = Math.ceil(measureText(note.text, { weight: 600, fontSize: g.labelPx })) + 2 * padX;
        const left = Math.min(g.frame.x + g.frame.width - w, Math.max(g.frame.x, p.x - w / 2));
        // Under the mark when the chip would leave the plot top, mirroring the value label's flip.
        const above = p.y - (anchorGap + gap + chipH) >= g.frame.y;
        const anchorY = above ? p.y - anchorGap : p.y + anchorGap;
        const top = above ? anchorY - gap - chipH : anchorY + gap;
        return (
          <g key={`${note.entityId}-${i}`} opacity={note.progress}>
            <line x1={p.x} y1={anchorY} x2={p.x} y2={above ? top + chipH : top} stroke={g.ink.emphasis} strokeWidth={TOKENS.lines.axis_stroke_px * g.scale} />
            <rect x={left} y={top} width={w} height={chipH} rx={g.unit / 2} fill={g.ink.surface2} stroke={g.ink.emphasis} strokeWidth={TOKENS.lines.grid_stroke_px * g.scale} />
            <text x={left + padX} y={top + chipH / 2} dominantBaseline="central" fontFamily={TEXT_STACK} fontSize={g.labelPx} fontWeight={600} fill={g.ink.primary}>
              {note.text}
            </text>
          </g>
        );
      })}
    </g>
  );
}

function Legend({ g, box }: { g: ChartGeometry; box: PixelBox }): ReactElement {
  const env = useSceneEnv();
  return (
    <div style={{ position: "absolute", left: box.x, top: box.y, width: box.width, height: box.height, display: "flex", alignItems: "center", gap: 4 * g.unit, overflow: "hidden", fontFamily: TEXT_STACK, fontSize: g.labelPx, color: g.ink.secondary, whiteSpace: "nowrap" }}>
      {g.template.series.map((binding, si) => {
        const entity = env.entities.get(binding.entity_id);
        return (
          <div key={binding.entity_id} style={{ display: "flex", alignItems: "center", gap: g.unit, opacity: entityOpacity(g.state, binding.entity_id) }}>
            <span style={{ width: 1.5 * g.unit, height: 1.5 * g.unit, borderRadius: g.unit / 4, background: g.seriesHex(si), flex: "0 0 auto" }} />
            <span>{entity?.short_label || entity?.label || binding.value}</span>
          </div>
        );
      })}
    </div>
  );
}

/** The pre-axis-region frame: tick labels and a top value label had to fit inside the plot itself. */
function insetFrame(plot: PixelBox, domain: ValueDomain, unitLabel: string, scale: number): PixelBox {
  const unit = TOKENS.layout.grid.unit * scale;
  const labelPx = rolePx("label", scale);
  const tickWidth = Math.max(0, ...niceTicks(domain).map((t) => measureText(formatValue(t, unitLabel), { weight: 500, fontSize: labelPx })));
  const left = Math.ceil(tickWidth) + 3 * unit;
  const top = lineHeightPx(labelPx) + 2 * unit;
  const bottom = lineHeightPx(labelPx) + 2 * unit + (domain.broken ? lineHeightPx(rolePx("caption", scale)) : 0);
  return { x: plot.x + left, y: plot.y + top, width: Math.max(1, plot.width - left - 2 * unit), height: Math.max(1, plot.height - top - bottom) };
}

export function ChartTemplate({ compiled, template, state }: TemplateProps<ChartSpec>): ReactElement {
  const env = useSceneEnv();
  const { width, height } = env.bundle.timeline;
  const scale = env.scale;
  const asset = env.assets.get(template.dataset_asset_id);
  const dataset = asset?.dataset_id ? env.datasets.get(asset.dataset_id) : undefined;
  if (!dataset) throw new Error(`scene ${compiled.scene_id}: dataset asset ${template.dataset_asset_id} is not in the bundle`);
  const colors = entityColors(compiled);
  const plot = regionOf(compiled, "plot", "content") ?? safeAreaBox(width, height);
  const axisX = regionOf(compiled, "axis_x");
  const axisY = regionOf(compiled, "axis_y");
  const titleBox = regionOf(compiled, "title");
  const legendBox = regionOf(compiled, "legend");
  const unit = TOKENS.layout.grid.unit * scale;
  const labelPx = rolePx("label", scale);
  const categories = chartCategories(dataset, template);
  const stacked = template.chart_kind === "stacked_bar";
  const domain = valueDomain(stacked ? stackedTotals(categories) : categories.flatMap((c) => c.values), template.baseline_zero);
  // The data frame is the compiled plot region, where qc.series_points samples the line; only a scene
  // without axis regions insets it so the tick labels fit inside the plot.
  const frame = axisX && axisY ? plot : insetFrame(plot, domain, template.y.unit, scale);
  const tickLabelX = (axisY ? axisY.x + axisY.width : frame.x) - 1.5 * unit;
  const xLabelY = axisX ? axisX.y + unit + 0.75 * labelPx : frame.y + frame.height + unit + labelPx;
  const n = Math.max(1, categories.length);
  const band = scaleBand<number>().domain(range(n)).range([frame.x, frame.x + frame.width]).paddingInner(0.3).paddingOuter(0.2);
  const xValues = categories.map((c) => numberOrNull(c.x));
  const xDomain = valueDomain(xValues, false);
  const xLinearScale = template.chart_kind === "scatter" ? scaleLinear().domain([xDomain.min, xDomain.max]).range([frame.x, frame.x + frame.width]) : null;
  const order = state.sort ? sortOrder(categories, state.sort.by) : null;
  const mask = state.filter ? filterMask(categories, dataset, state.filter) : null;
  const g: ChartGeometry = {
    template,
    state,
    categories,
    slots: categories.map((_, i) => categorySlot(i, order, mask, state.sort?.progress ?? 0, state.filter?.progress ?? 0)),
    frame,
    y: valueScale(frame, domain),
    bandLeft: (slot) => (band(0) ?? frame.x) + slot * band.step(),
    bandWidth: band.bandwidth(),
    pointX: pointScale(frame, categories.length),
    xLinear: xLinearScale === null ? null : (v) => xLinearScale(v),
    seriesHex: (i) => entityHex(colors, template.series[i]?.entity_id ?? "", categoricalTokenId(i)),
    scale,
    unit,
    labelPx,
    tickLabelX,
    xLabelY,
    ink: {
      primary: tokenHex("ui.ink.primary"),
      secondary: tokenHex("ui.ink.secondary"),
      muted: tokenHex("ui.ink.muted"),
      emphasis: tokenHex("state.emphasis"),
      surface2: tokenHex("ui.surface.2"),
      strokeSoft: tokenHex("ui.stroke.soft"),
    },
  };
  const clipPrefix = `${compiled.scene_id}-chart`;
  const layerOf = (layer: Layer): ReactElement | null =>
    template.chart_kind === "bar" ? (
      <Bars g={g} layer={layer} />
    ) : template.chart_kind === "stacked_bar" ? (
      <StackedBars g={g} layer={layer} />
    ) : template.chart_kind === "scatter" ? (
      layer === "marks" ? <Scatter g={g} /> : null
    ) : template.chart_kind === "slope" ? (
      <Slope g={g} clipPrefix={clipPrefix} layer={layer} />
    ) : (
      <Lines g={g} filled={template.chart_kind === "area"} clipPrefix={clipPrefix} layer={layer} />
    );
  const camera = cameraTransform(state.camera);
  // One stroke of clip padding, so a mark on the frame edge draws whole; text is never clipped at all.
  const clipPad = TOKENS.lines.data_stroke_px * scale;
  return (
    <>
      {titleBox ? (
        <div style={{ position: "absolute", left: titleBox.x, top: titleBox.y, width: titleBox.width, height: titleBox.height, overflow: "hidden", fontFamily: DISPLAY_STACK, fontWeight: 600, fontSize: rolePx("h2", scale), lineHeight: `${lineHeightPx(rolePx("h2", scale))}px`, color: g.ink.primary, whiteSpace: "nowrap" }}>
          {chartTitle(dataset, template)}
        </div>
      ) : null}
      {legendBox ? <Legend g={g} box={legendBox} /> : null}
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ position: "absolute", left: 0, top: 0 }}>
        <defs>
          <clipPath id={`${clipPrefix}-plot`}>
            <rect x={plot.x - clipPad} y={plot.y - clipPad} width={plot.width + 2 * clipPad} height={plot.height + 2 * clipPad} />
          </clipPath>
        </defs>
        <g transform={camera}>
          <Axes g={g} domain={domain} />
        </g>
        <g clipPath={`url(#${clipPrefix}-plot)`}>
          <g transform={camera}>{layerOf("marks")}</g>
        </g>
        <g transform={camera}>
          {layerOf("labels")}
          <Compare g={g} />
          <Annotations g={g} />
        </g>
      </svg>
    </>
  );
}
