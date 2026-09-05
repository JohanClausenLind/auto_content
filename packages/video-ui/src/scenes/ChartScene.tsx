import type { ChartScene as Spec, DatasetTable } from "@content-factory/content-schema-ts";
import { arc, bin, scaleLinear, stack, type DefaultArcObject } from "d3";
import { FONT_STACK, fontStackFor, formatNumber, refClassification, textColor } from "@content-factory/content-ui";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { useSceneEnv } from "../context";
import { enter, motionFrames, progress } from "../motion";
import { Lines, SceneFrame, citationLine, useFittedText, useSceneGeometry } from "./common";

export interface SeriesPoint {
  label: string;
  values: number[];
}

/** Rows → labelled numeric points for the declared x column and y series. Pure and total:
 * non-numeric cells become 0 so a bad row can never crash a render. */
export function chartPoints(dataset: DatasetTable | undefined, x: string, y: readonly string[]): SeriesPoint[] {
  if (!dataset) return [];
  return dataset.rows.map((row) => ({
    label: String(row[x] ?? ""),
    values: y.map((column) => {
      const value = row[column];
      return typeof value === "number" && Number.isFinite(value) ? value : 0;
    }),
  }));
}

/** One column as numbers, in row order. Non-numeric cells become 0 (same totality as chartPoints);
 * scatter needs a numeric x, where the bar family only ever needed the label. */
export function numericColumn(dataset: DatasetTable | undefined, column: string): number[] {
  if (!dataset) return [];
  return dataset.rows.map((row) => {
    const value = row[column];
    return typeof value === "number" && Number.isFinite(value) ? value : 0;
  });
}

export function niceMax(values: readonly number[]): number {
  const max = Math.max(0, ...values);
  if (max === 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(max));
  const normalized = max / magnitude;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return step * magnitude;
}

/** Per-point totals across every series — the domain a stacked bar actually reaches. */
export function stackTotals(points: readonly SeriesPoint[]): number[] {
  return points.map((point) => point.values.reduce((sum, value) => sum + Math.max(0, value), 0));
}

/** Running start/end pairs: each waterfall bar begins where the previous one closed. */
export function waterfallSteps(values: readonly number[]): { start: number; end: number }[] {
  let running = 0;
  return values.map((delta) => {
    const start = running;
    running += delta;
    return { start, end: running };
  });
}

/** Cumulative slice angles in radians for a donut, clockwise from 12 o'clock.
 * Empty when nothing positive is present, so a zero dataset draws no ring rather than NaN paths. */
export function donutAngles(values: readonly number[]): { start: number; end: number }[] {
  const total = values.reduce((sum, value) => sum + Math.max(0, value), 0);
  if (total <= 0) return [];
  let acc = 0;
  return values.map((value) => {
    const start = (acc / total) * Math.PI * 2;
    acc += Math.max(0, value);
    return { start, end: (acc / total) * Math.PI * 2 };
  });
}

/** Every ChartKind the component draws for real. Anything outside this set still renders (as bars)
 * with the requested kind named on screen — honest, never silently wrong. */
const SUPPORTED = new Set([
  "bar",
  "horizontal_bar",
  "line",
  "area",
  "stacked_bar",
  "donut",
  "histogram",
  "scatter",
  "bubble",
  "waterfall",
  "step",
]);

const HISTOGRAM_BINS = 8;

/**
 * An SVG path through the points as a staircase: hold the value, then jump.
 *
 * The reason a step chart is not a nicety: a line drawn between two monthly readings claims the
 * value passed through every point on the slope, and for a quantity that only ever changes at a
 * known moment — a tariff, a policy rate, a headcount — that claim is false. The staircase says
 * "this held, then it changed", which is what the data means. Drawn as `hv` (hold, then jump), so
 * each value owns the interval that follows its own reading rather than the one before it.
 */
export function stepPath(points: readonly { x: number; y: number }[], endX?: number): string {
  if (points.length === 0) return "";
  const first = points[0]!;
  const parts = [`M ${first.x} ${first.y}`];
  for (const p of points.slice(1)) {
    parts.push(`H ${p.x}`, `V ${p.y}`);
  }
  // The last reading holds to the end of the plot. Without it the final riser lands on the frame
  // edge and the newest value — the one the chart is usually about — is a line of zero length.
  const last = points.at(-1)!;
  if (endX !== undefined && endX > last.x) parts.push(`H ${endX}`);
  return parts.join(" ");
}

/**
 * Animated data chart drawn as plain SVG. D3 supplies the geometry that is genuinely hard —
 * arc paths, stack offsets, histogram thresholds — and React owns every element, so the frame
 * remains the only clock and two renders of the same frame produce identical bytes. No
 * d3.select, no d3.transition, no CSS animation anywhere in this file.
 */
export function ChartScene({ scene }: { scene: Spec; compiled: unknown }): ReactElement {
  const frame = useCurrentFrame();
  const { bundle, theme } = useSceneEnv();
  const { safe, scale, fps, portrait, align } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const t = progress(frame, Math.round(f.fast / 2), f.countUp, theme.motion.easing.decelerate);

  const dataset = bundle.datasets?.[scene.data.dataset_id];
  const points = chartPoints(dataset, scene.x, scene.y);
  const title = useFittedText(scene.title.text, portrait ? "headline" : "label", safe.width, Math.round(safe.height * 0.16), 2, "paper");
  const fallback = !SUPPORTED.has(scene.chart);
  const kind = fallback ? "bar" : scene.chart;
  // `scene.source_ids` credits what the *chart* claims; the dataset's own `source_ids` say where
  // the table came from. A chart that names neither is credited by its dataset label alone, which
  // is the pre-existing behaviour and still the honest one for a derived table.
  const credits = (scene.source_ids.length > 0 ? scene.source_ids : (dataset?.source_ids ?? []))
    .map((id) => {
      const card = bundle.sources[id];
      return card ? citationLine(card) : id;
    });

  const chartWidth = safe.width;
  const stepped = scene.chart === "step";
  const chartHeight = Math.round(safe.height * (portrait ? 0.56 : 0.58));
  const axisColor = theme.color.rule;
  const valueColor = textColor(theme, "body", "paper");
  const tickColor = textColor(theme, "caption", "paper");
  const seriesColors = [theme.color.accent, theme.color.ink, theme.color.muted];
  const colorAt = (i: number): string => seriesColors[i % seriesColors.length]!;
  // Donut and stacked bars are categorical with more than three members, so they take the
  // theme's colourblind-safe series palette instead of cycling three UI colours.
  const categoryAt = (i: number): string => theme.series[i % theme.series.length]?.color ?? colorAt(i);

  const pad = Math.round(12 * scale);
  const plotW = chartWidth - pad * 2;
  const plotH = chartHeight - pad * 2;
  const n = Math.max(1, points.length);
  const firstValues = points.map((p) => p.values[0] ?? 0);

  let body: ReactElement | null = null;
  let labels: string[] = points.map((p) => p.label);
  let showAxes = true;

  if (kind === "bar" || kind === "horizontal_bar") {
    // Value labels sit on every bar, so the domain needs no round axis ceiling: the tallest bar
    // fills the plot (with headroom for its label) instead of stopping at 42 % under a 50-tick.
    const max = Math.max(1e-9, ...points.flatMap((p) => p.values)) * 1.06;
    const horizontal = kind === "horizontal_bar";
    const labelRoom = horizontal ? 0 : Math.round(34 * scale); // x labels drawn inside the plot box
    const band = (horizontal ? plotH : plotW) / n;
    const thickness = Math.min(band * (portrait ? 0.68 : 0.6), 120 * scale);
    const usableH = plotH - labelRoom - Math.round(40 * scale); // room for value labels above
    // Bars grow one after another (each over half the count-up, staggered across the rest) so the
    // eye follows the series instead of watching one block rise; the latest value carries the
    // accent, earlier ones the muted colour — the highlight is the point of the chart.
    const grow = Math.max(1, Math.round(f.countUp * 0.5));
    const stagger = n > 1 ? Math.max(1, Math.round((f.countUp * 0.5) / (n - 1))) : 0;
    const valueFont = Math.round((portrait ? 30 : 24) * scale);
    const tickFont = Math.round((portrait ? 26 : 22) * scale);
    body = (
      <g>
        {points.map((point, i) => {
          const value = point.values[0] ?? 0;
          const tb = progress(frame, Math.round(f.fast / 2) + stagger * i, grow, theme.motion.easing.decelerate);
          const grown = (value / max) * (horizontal ? plotW : usableH) * tb;
          const along = pad + band * i + (band - thickness) / 2;
          const fill = i === n - 1 ? seriesColors[0] : theme.color.muted;
          const shown = formatNumber(value, "auto", "").numeral;
          if (horizontal) {
            return <rect key={i} x={pad} y={along} width={grown} height={thickness} rx={4 * scale} fill={fill} />;
          }
          const top = pad + usableH - grown + Math.round(40 * scale);
          return (
            <g key={i}>
              <rect x={along} y={top} width={thickness} height={grown} rx={4 * scale} fill={fill} />
              <text
                x={along + thickness / 2}
                y={top - Math.round(12 * scale)}
                textAnchor="middle"
                fontFamily={fontStackFor(theme.type.headline.family)}
                fontWeight={700}
                fontSize={valueFont}
                fill={i === n - 1 ? seriesColors[0] : valueColor}
                opacity={tb > 0.9 ? (tb - 0.9) / 0.1 : 0}
              >
                {shown}
              </text>
              <text x={along + thickness / 2} y={pad + plotH - Math.round(4 * scale)} textAnchor="middle" fontFamily={FONT_STACK} fontSize={tickFont} fill={tickColor}>
                {point.label}
              </text>
            </g>
          );
        })}
      </g>
    );
    if (!horizontal) labels = []; // drawn inside the svg, under each bar
  } else if (kind === "stacked_bar") {
    // d3.stack computes the y0/y1 offsets; the reshape is one row object per point.
    const keys = scene.y.map((_, i) => `s${i}`);
    const rows = points.map((point) => {
      const row: Record<string, number> = {};
      point.values.forEach((value, i) => {
        row[`s${i}`] = Math.max(0, value);
      });
      return row;
    });
    const layers = stack<Record<string, number>>().keys(keys)(rows);
    const max = niceMax(stackTotals(points));
    const band = plotW / n;
    const thickness = Math.min(band * 0.6, 90 * scale);
    body = (
      <g>
        {layers.map((layer, series) => (
          <g key={series}>
            {layer.map((segment, i) => {
              const [y0, y1] = segment;
              const top = pad + plotH - (y1 / max) * plotH * t;
              const height = ((y1 - y0) / max) * plotH * t;
              return <rect key={i} x={pad + band * i + (band - thickness) / 2} y={top} width={thickness} height={height} fill={categoryAt(series)} />;
            })}
          </g>
        ))}
      </g>
    );
  } else if (kind === "waterfall") {
    const steps = waterfallSteps(firstValues);
    const max = niceMax(steps.flatMap((s) => [Math.abs(s.start), Math.abs(s.end)]));
    const band = plotW / n;
    const thickness = Math.min(band * 0.6, 90 * scale);
    body = (
      <g>
        {steps.map((step, i) => {
          const top = pad + plotH - (Math.max(step.start, step.end) / max) * plotH * t;
          const height = (Math.abs(step.end - step.start) / max) * plotH * t;
          const rising = step.end >= step.start;
          return (
            <rect
              key={i}
              x={pad + band * i + (band - thickness) / 2}
              y={top}
              width={thickness}
              height={height}
              fill={rising ? seriesColors[0] : theme.color.muted}
            />
          );
        })}
      </g>
    );
  } else if (kind === "histogram") {
    const bins = bin().thresholds(HISTOGRAM_BINS)(firstValues);
    const max = niceMax(bins.map((b) => b.length));
    const band = plotW / Math.max(1, bins.length);
    body = (
      <g>
        {bins.map((b, i) => {
          const height = (b.length / max) * plotH * t;
          return <rect key={i} x={pad + band * i + band * 0.06} y={pad + plotH - height} width={band * 0.88} height={height} fill={seriesColors[0]} />;
        })}
      </g>
    );
    labels = bins.map((b) => String(b.x0 ?? ""));
  } else if (kind === "donut") {
    // The contract already refuses a donut with more than one series, so slices are the rows.
    const angles = donutAngles(firstValues);
    const radius = (Math.min(plotW, plotH) / 2) * 0.92;
    const shape = arc<DefaultArcObject>();
    const cx = pad + plotW / 2;
    const cy = pad + plotH / 2;
    showAxes = false;
    body = (
      <g transform={`translate(${cx},${cy})`}>
        {angles.map((angle, i) => {
          const d = shape({
            innerRadius: radius * 0.58,
            outerRadius: radius,
            startAngle: angle.start,
            endAngle: angle.start + (angle.end - angle.start) * t,
            padAngle: 0,
          });
          return d === null ? null : <path key={i} d={d} fill={categoryAt(i)} stroke={theme.color.paper} strokeWidth={2 * scale} />;
        })}
      </g>
    );
  } else if (kind === "scatter" || kind === "bubble") {
    const xs = numericColumn(dataset, scene.x);
    const ys = firstValues;
    const radii = points.map((p) => p.values[1] ?? 0);
    const x = scaleLinear().domain([0, niceMax(xs)]).range([pad, pad + plotW]);
    const y = scaleLinear().domain([0, niceMax(ys)]).range([pad + plotH, pad]);
    const maxRadius = niceMax(radii);
    const dot = 7 * scale;
    // Points appear in row order as the reveal advances, so the motion stays frame-derived.
    const shown = Math.ceil(points.length * t);
    body = (
      <g>
        {points.map((_, i) =>
          i >= shown ? null : (
            <circle
              key={i}
              cx={x(xs[i] ?? 0)}
              cy={y(ys[i] ?? 0)}
              r={kind === "bubble" ? dot + Math.sqrt((radii[i] ?? 0) / maxRadius) * dot * 2.2 : dot}
              fill={seriesColors[0]}
              fillOpacity={0.72}
            />
          ),
        )}
      </g>
    );
    labels = [];
  } else {
    const max = niceMax(points.flatMap((p) => p.values));
    // A step chart's readings are intervals, not instants: n readings need n intervals, so the
    // x scale divides by n and each value holds until the next one. Line and area interpolate
    // between instants and keep the n-1 scale that puts the last point on the right edge.
    const span = stepped ? plotW / n : plotW / Math.max(1, n - 1);
    // A stepped chart labels its intervals inside the plot, above the axis — the same arrangement
    // the bar family uses — so the staircase itself is drawn in what is left above the labels.
    const labelRoom = stepped ? Math.round(34 * scale) : 0;
    const usableH = plotH - labelRoom;
    const coords = (series: number) =>
      points.map((point, i) => ({
        x: pad + span * i,
        y: pad + usableH - ((point.values[series] ?? 0) / max) * usableH,
      }));
    const shown = Math.max(2, Math.ceil(n * t));
    // A step's labels name intervals, so they sit under the middle of the interval they name. The
    // line family's own labels are spread edge to edge outside the plot, which for a staircase
    // would put "after" at the right edge while its riser starts two thirds of the way across —
    // reading as a jump that happened before the label it belongs to.
    const stepLabels = stepped ? (
      <g>
        {points.map((point, i) => (
          <text
            key={`lbl-${i}`}
            x={pad + span * i + span / 2}
            y={pad + plotH - Math.round(6 * scale)}
            textAnchor="middle"
            fontFamily={FONT_STACK}
            fontSize={Math.round((portrait ? 26 : 22) * scale)}
            fill={tickColor}
          >
            {point.label}
          </text>
        ))}
      </g>
    ) : null;
    if (stepped) labels = [];
    body = (
      <g>
        {stepLabels}
        {scene.y.map((_, series) => {
          const pts = coords(series).slice(0, shown);
          const path = stepped
            ? stepPath(pts, pts.length === n ? pad + plotW : undefined)
            : pts.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
          const color = colorAt(series);
          return (
            <g key={series}>
              {kind === "area" && (
                <path
                  d={`${path} L ${pts.at(-1)?.x ?? pad} ${pad + usableH} L ${pad} ${pad + usableH} Z`}
                  fill={color}
                  opacity={0.18}
                />
              )}
              <path d={path} fill="none" stroke={color} strokeWidth={4 * scale} strokeLinejoin="round" />
            </g>
          );
        })}
      </g>
    );
  }

  return (
    <SceneFrame testId="chart" justify={portrait ? "center" : "start"} notice={refClassification(scene.data, bundle.datasets)}>
      <Lines block={title} align={align} style={enter(frame, 0, f.base, theme, 10 * scale)} />
      <svg
        width={chartWidth}
        height={chartHeight}
        viewBox={`0 0 ${chartWidth} ${chartHeight}`}
        style={{ marginTop: 16 * scale, ...enter(frame, Math.round(f.fast / 2), f.base, theme, 14 * scale) }}
        aria-hidden="true"
      >
        {showAxes && (
          <>
            <line x1={pad} y1={pad + plotH} x2={pad + plotW} y2={pad + plotH} stroke={axisColor} strokeWidth={2} />
            <line x1={pad} y1={pad} x2={pad} y2={pad + plotH} stroke={axisColor} strokeWidth={2} />
          </>
        )}
        {body}
      </svg>
      {labels.length > 0 ? (
        <div style={{ display: "flex", justifyContent: "space-between", width: chartWidth, marginTop: 6 * scale, color: tickColor, fontSize: 22 * scale }}>
          {labels.map((label, i) => (
            <span key={i}>{label}</span>
          ))}
        </div>
      ) : null}
      <div style={{ marginTop: 8 * scale, width: chartWidth, textAlign: align, color: tickColor, fontSize: 20 * scale }}>
        {dataset ? `${dataset.label}${dataset.unit ? ` (${dataset.unit})` : ""}` : `dataset ${scene.data.dataset_id} missing`}
        {fallback && ` — ${scene.chart} drawn as bars`}
        {scene.caption ? ` · ${scene.caption.text}` : ""}
        {credits.length > 0 ? ` · ${theme.citation.prefix}: ${credits.join("; ")}` : ""}
      </div>
    </SceneFrame>
  );
}
