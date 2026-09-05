// Deterministic number formatting (no Intl: ICU versions differ between Node and the headless
// browser). English conventions: comma thousands, point decimals.
import type { DataRef, DatasetTable, NumberLayer } from "@content-factory/content-schema-ts";

export type NumberFormat = NumberLayer["format"];

export interface FormattedNumber {
  /** The numeral as displayed, e.g. "1,234" or "21" or "3.4M". */
  numeral: string;
  /** Unit rendered after the numeral (may be empty). */
  unit: string;
  /** Prefix rendered before the numeral (currency symbol), may be empty. */
  prefix: string;
  /** Full plain-text form, e.g. "21%" or "$1,234" or "3.4M t". */
  text: string;
}

const MISSING = "—";

export function groupThousands(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** Fixed decimals without exponent drift, trailing zeros trimmed when `trim` is set. */
export function toDecimal(value: number, decimals: number, trim = false): string {
  const neg = value < 0 || (value === 0 && 1 / value < 0);
  const abs = Math.abs(value);
  const scaled = Math.round(abs * 10 ** decimals);
  const s = scaled.toString().padStart(decimals + 1, "0");
  const intPart = decimals === 0 ? s : s.slice(0, s.length - decimals);
  let frac = decimals === 0 ? "" : s.slice(s.length - decimals);
  if (trim) frac = frac.replace(/0+$/, "");
  const body = groupThousands(intPart) + (frac.length > 0 ? `.${frac}` : "");
  return neg && body !== "0" ? `−${body}` : body;
}

export function formatCompact(value: number): string {
  const abs = Math.abs(value);
  const units: Array<[number, string]> = [
    [1e12, "T"],
    [1e9, "B"],
    [1e6, "M"],
    [1e3, "K"],
  ];
  for (const [div, suffix] of units) {
    if (abs >= div) {
      const v = value / div;
      return toDecimal(v, Math.abs(v) < 10 ? 1 : 0, true) + suffix;
    }
  }
  return toDecimal(value, Number.isInteger(value) ? 0 : 1, true);
}

export function formatNumber(value: number | string | null | undefined, format: NumberFormat, unit = ""): FormattedNumber {
  const n = typeof value === "string" ? Number(value.replace(/[,\s]/g, "")) : value;
  if (n === null || n === undefined || Number.isNaN(n) || !Number.isFinite(n)) {
    return { numeral: MISSING, unit: "", prefix: "", text: MISSING };
  }
  switch (format) {
    case "integer":
      return join(toDecimal(n, 0), unit);
    case "percent":
      return join(toDecimal(n, Number.isInteger(n) ? 0 : 1), unit.length > 0 ? unit : "%");
    case "compact":
      return join(formatCompact(n), unit);
    case "currency": {
      const decimals = Number.isInteger(n) ? 0 : 2;
      const numeral = toDecimal(n, decimals);
      if (unit.length > 0 && unit.length <= 1) {
        return { numeral, unit: "", prefix: unit, text: `${unit}${numeral}` };
      }
      if (/^[A-Z]{3}$/.test(unit)) {
        return { numeral, unit, prefix: "", text: `${numeral} ${unit}` };
      }
      return join(numeral, unit);
    }
    default: {
      const numeral = Number.isInteger(n) ? toDecimal(n, 0) : toDecimal(n, 2, true);
      return join(numeral, unit);
    }
  }
}

function join(numeral: string, unit: string): FormattedNumber {
  const tight = unit === "%" || unit === "‰" || unit === "°" || unit === "′" || unit === "″";
  const text = unit.length === 0 ? numeral : tight ? `${numeral}${unit}` : `${numeral} ${unit}`;
  return { numeral, unit, prefix: "", text };
}

export type CellValue = string | number | null;

/**
 * Resolve a DataRef through the bundle datasets: `row_key` matches the first column's value
 * (or a column literally named `key`/`row_key`); `column` defaults to the last column.
 */
export function resolveDataRef(ref: DataRef, datasets: Readonly<Record<string, DatasetTable>>): CellValue | undefined {
  const table = datasets[ref.dataset_id];
  if (!table) return undefined;
  const keyColumn = table.columns.find((c) => c === "key" || c === "row_key") ?? table.columns[0];
  const row =
    ref.row_key === null
      ? table.rows[0]
      : table.rows.find((r) => String(r[keyColumn] ?? "") === ref.row_key);
  if (!row) return undefined;
  const column = ref.column ?? table.columns[table.columns.length - 1] ?? keyColumn;
  const v = row[column];
  return v === undefined ? undefined : v;
}

export function resolveNumber(ref: DataRef, datasets: Readonly<Record<string, DatasetTable>>): number | null {
  const v = resolveDataRef(ref, datasets);
  if (v === undefined || v === null) return null;
  const n = typeof v === "number" ? v : Number(String(v).replace(/[,\s]/g, ""));
  return Number.isFinite(n) ? n : null;
}

export type DataClassification = DatasetTable["classification"];

/**
 * The classification of the dataset a DataRef points at, or `undefined` when the table is absent.
 *
 * The reason this exists is the pair `ESTIMATE` and `ILLUSTRATIVE`. A table marked either of those
 * is not a measurement — an illustrative table is a *shape*, drawn to explain a mechanism, and a
 * figure lifted out of one and set in 200-point type is indistinguishable on screen from a figure
 * that came off a source. `DatasetTable.classification` has carried that distinction since the
 * contract was written and no renderer read it, so the caveat existed only in the JSON.
 */
export function refClassification(
  ref: DataRef | null | undefined,
  datasets: Readonly<Record<string, DatasetTable>>,
): DataClassification | undefined {
  if (!ref) return undefined;
  return datasets[ref.dataset_id]?.classification;
}

/** The words shown on screen for a classification that needs a caveat; `null` for measured data. */
export function classificationNotice(c: DataClassification | undefined): string | null {
  if (c === "ILLUSTRATIVE") return "illustrative — not measured";
  if (c === "ESTIMATE") return "estimate";
  return null;
}
