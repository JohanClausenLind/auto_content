// Chart data and geometry helpers: pure, so the tests and the SVG template share one truth.
import type { ChartTemplate, EvidenceDataset, FilterAction, PixelBox, SortAction } from "@content-factory/content-schema-ts";
import { toDecimal } from "@content-factory/content-ui";
import { range, scaleLinear, scalePoint } from "d3";

import { lerp } from "../geometry";

export type Cell = number | string | null;

export interface ChartCategory {
  key: string;
  label: string;
  x: Cell;
  /** One entry per series binding; null is a gap, never zero. */
  values: (number | null)[];
  /** The row the category came from, for filters on any column. */
  cells: readonly Cell[];
}

export interface ValueDomain {
  min: number;
  max: number;
  /** True when a non-zero baseline was allowed and the axis does not reach zero. */
  broken: boolean;
}

export interface StackSegment {
  series: number;
  y0: number;
  y1: number;
  value: number;
}

export interface Slot {
  position: number;
  alpha: number;
}

export function numberOrNull(cell: Cell | undefined): number | null {
  return typeof cell === "number" && Number.isFinite(cell) ? cell : null;
}

export function cellLabel(cell: Cell | undefined): string {
  return cell === null || cell === undefined ? "" : String(cell);
}

export function columnIndex(dataset: EvidenceDataset, name: string): number {
  const i = dataset.columns.findIndex((c) => c.name === name);
  if (i < 0) throw new Error(`dataset ${dataset.dataset_id} has no column ${name}`);
  return i;
}

/** Rows → categories; wide data names a column per series, long data names a series field. */
export function chartCategories(dataset: EvidenceDataset, template: ChartTemplate): ChartCategory[] {
  const xi = columnIndex(dataset, template.x.field);
  if (template.series_field === null) {
    // Wide: each series names its column; the contract lets a lone series leave it empty and draw y.field.
    const cols = template.series.map((s) => columnIndex(dataset, s.value === "" ? template.y.field : s.value));
    return dataset.rows.map((row) => ({
      key: row.key,
      label: cellLabel(row.values[xi]),
      x: row.values[xi] ?? null,
      values: cols.map((c) => numberOrNull(row.values[c])),
      cells: row.values,
    }));
  }
  const si = columnIndex(dataset, template.series_field);
  const yi = columnIndex(dataset, template.y.field);
  const groups = new Map<string, ChartCategory>();
  for (const row of dataset.rows) {
    const x = row.values[xi] ?? null;
    const key = cellLabel(x);
    let category = groups.get(key);
    if (!category) {
      category = { key, label: key, x, values: template.series.map(() => null), cells: row.values };
      groups.set(key, category);
    }
    const series = template.series.findIndex((b) => b.value === cellLabel(row.values[si]));
    if (series >= 0) category.values[series] = numberOrNull(row.values[yi]);
  }
  return [...groups.values()];
}

/** Nice axis bounds; a zero baseline always includes zero, anything else is disclosed as broken. */
export function valueDomain(values: readonly (number | null)[], baselineZero: boolean): ValueDomain {
  const finite = values.filter((v): v is number => v !== null);
  if (finite.length === 0) return { min: 0, max: 1, broken: false };
  let lo = Math.min(...finite);
  let hi = Math.max(...finite);
  if (baselineZero) {
    lo = Math.min(0, lo);
    hi = Math.max(0, hi);
  }
  if (lo === hi) {
    if (lo === 0) hi = 1;
    else if (lo > 0) lo = 0;
    else hi = 0;
  }
  const [nlo, nhi] = scaleLinear().domain([lo, hi]).nice().domain();
  const min = nlo ?? lo;
  const max = nhi ?? hi;
  return { min, max, broken: !baselineZero && (min > 0 || max < 0) };
}

export function niceTicks(domain: ValueDomain, count = 5): number[] {
  return scaleLinear().domain([domain.min, domain.max]).ticks(count);
}

/** Pixel y of a value over `plot`: its bottom edge at the domain min, its top edge at the max. */
export function valueScale(plot: PixelBox, domain: ValueDomain): (value: number) => number {
  const scale = scaleLinear().domain([domain.min, domain.max]).range([plot.y + plot.height, plot.y]);
  return (value) => scale(value);
}

/** d3 scalePoint().padding(0.5) over the plot width; `position` may be fractional while a sort runs. */
export function pointScale(plot: PixelBox, n: number): (position: number) => number {
  const scale = scalePoint<number>().domain(range(Math.max(1, n))).range([plot.x, plot.x + plot.width]).padding(0.5);
  const x0 = scale(0) ?? plot.x;
  return (position) => x0 + position * scale.step();
}

/** The [x, y] points a line or area series draws inside `plot`, gaps skipped; the QC samples the same. */
export function seriesPolyline(template: ChartTemplate, dataset: EvidenceDataset, binding: ChartTemplate["series"][number], plot: PixelBox): [number, number][] {
  const si = template.series.findIndex((s) => s.entity_id === binding.entity_id);
  if (si < 0) return [];
  const categories = chartCategories(dataset, template);
  const y = valueScale(plot, valueDomain(categories.flatMap((c) => c.values), template.baseline_zero));
  const x = pointScale(plot, categories.length);
  const out: [number, number][] = [];
  categories.forEach((c, i) => {
    const v = c.values[si];
    if (v !== null && v !== undefined) out.push([x(i), y(v)]);
  });
  return out;
}

export type LabelSide = "above" | "below" | "left";

/** Above the mark unless that leaves the plot top; then below it, or inside-left for the last mark. */
export function valueLabelSide(markY: number, plot: PixelBox, labelHeightPx: number, isLast: boolean): LabelSide {
  if (markY - plot.y >= labelHeightPx) return "above";
  return isLast ? "left" : "below";
}

/** Sum of the present values per category: the height a stacked bar reaches. */
export function stackedTotals(categories: readonly ChartCategory[]): number[] {
  return categories.map((c) => c.values.reduce<number>((sum, v) => sum + (v ?? 0), 0));
}

/** Positive values stack upward from zero and negatives downward; a null leaves no segment. */
export function stackCategory(values: readonly (number | null)[]): StackSegment[] {
  let up = 0;
  let down = 0;
  const out: StackSegment[] = [];
  values.forEach((v, series) => {
    if (v === null) return;
    if (v >= 0) {
      out.push({ series, y0: up, y1: up + v, value: v });
      up += v;
    } else {
      out.push({ series, y0: down + v, y1: down, value: v });
      down += v;
    }
  });
  return out;
}

/** For each original index, its rank after sorting; categories without values sort last. */
export function sortOrder(categories: readonly ChartCategory[], by: SortAction["by"]): number[] {
  const totals = stackedTotals(categories);
  const hasValue = categories.map((c) => c.values.some((v) => v !== null));
  const indices = categories.map((_, i) => i);
  indices.sort((a, b) => {
    if (by === "label") return (categories[a]?.label ?? "").localeCompare(categories[b]?.label ?? "") || a - b;
    if (hasValue[a] !== hasValue[b]) return hasValue[a] ? -1 : 1;
    const d = (totals[a] ?? 0) - (totals[b] ?? 0);
    return (by === "value_desc" ? -d : d) || a - b;
  });
  const order = categories.map(() => 0);
  indices.forEach((original, rank) => {
    order[original] = rank;
  });
  return order;
}

export function cellMatches(cell: Cell, op: FilterAction["op"], value: number | string): boolean {
  if (op === "eq") return cellLabel(cell) === String(value);
  if (op === "neq") return cellLabel(cell) !== String(value);
  const a = numberOrNull(cell);
  const b = typeof value === "number" ? value : Number(value);
  if (a === null || !Number.isFinite(b)) return false;
  switch (op) {
    case "gt":
      return a > b;
    case "gte":
      return a >= b;
    case "lt":
      return a < b;
    case "lte":
      return a <= b;
    default:
      return false;
  }
}

/** Which categories a filter keeps; an unknown field keeps everything. */
export function filterMask(categories: readonly ChartCategory[], dataset: EvidenceDataset, filter: Pick<FilterAction, "field" | "op" | "value">): boolean[] {
  const fi = dataset.columns.findIndex((c) => c.name === filter.field);
  return categories.map((c) => (fi < 0 ? true : cellMatches(c.cells[fi] ?? null, filter.op, filter.value)));
}

/** Where category `i` sits and how opaque it is while a sort and a filter are in progress. */
export function categorySlot(i: number, order: readonly number[] | null, mask: readonly boolean[] | null, sortP: number, filterP: number): Slot {
  const sorted = order?.[i] ?? i;
  const afterSort = lerp(i, sorted, sortP);
  if (mask === null) return { position: afterSort, alpha: 1 };
  if (!mask[i]) return { position: afterSort, alpha: 1 - filterP };
  const keptRanks = mask
    .map((keep, j) => (keep ? (order?.[j] ?? j) : null))
    .filter((r): r is number => r !== null)
    .sort((a, b) => a - b);
  return { position: lerp(afterSort, keptRanks.indexOf(sorted), filterP), alpha: 1 };
}

/** Consecutive runs of present points; a null splits the line into a gap. */
export function lineSegments<P>(points: readonly (P | null)[]): P[][] {
  const out: P[][] = [];
  let current: P[] = [];
  for (const p of points) {
    if (p === null) {
      if (current.length > 0) out.push(current);
      current = [];
    } else current.push(p);
  }
  if (current.length > 0) out.push(current);
  return out;
}

/** Deterministic value label: up to two decimals, trimmed, unit attached or spaced. */
export function formatValue(value: number, unit = ""): string {
  const abs = Math.abs(value);
  const decimals = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  const numeral = toDecimal(value, decimals, true);
  if (unit === "") return numeral;
  return /^[%‰°]/.test(unit) ? `${numeral}${unit}` : `${numeral} ${unit}`;
}

export function polylineLength(points: readonly { x: number; y: number }[]): number {
  let length = 0;
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    if (a && b) length += Math.hypot(b.x - a.x, b.y - a.y);
  }
  return length;
}
