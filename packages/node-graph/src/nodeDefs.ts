/**
 * Node definitions: what a node type looks like and what it accepts.
 *
 * A definition is data, not code. The editor renders slots and widgets from it, the model
 * validates against it, and a catalogue is just a list of them, so an app can describe its own
 * node types (this repo's pipeline stages, for one) without touching the editor.
 */

export type WidgetValue = string | number | boolean;

export type WidgetKind =
  | "combo"
  | "chips"
  | "int"
  | "float"
  | "text"
  | "textarea"
  | "toggle"
  | "seed";

export interface WidgetSpec {
  readonly name: string;
  readonly kind: WidgetKind;
  /** Row label; defaults to `name`. */
  readonly label?: string;
  readonly default: WidgetValue;
  /** combo and chips: the choices, in order. */
  readonly options?: readonly string[];
  /** int/float/seed bounds and stepping. */
  readonly min?: number;
  readonly max?: number;
  readonly step?: number;
  readonly precision?: number;
  /** text/textarea affordances. */
  readonly placeholder?: string;
  readonly rows?: number;
  /** Shown as the row's title attribute. */
  readonly help?: string;
  /** A text/textarea/chips widget that must not be left empty for the graph to be valid. */
  readonly required?: boolean;
  /**
   * What to put here, in the operator's words, for a required widget that is empty. The twin of
   * `SlotSpec.hint`: "Audio File: dropped file is empty" says what is wrong and not what to do
   * about it, and a lane template that opens with an empty file input needs the second thing.
   */
  readonly hint?: string;
}

export interface SlotSpec {
  readonly name: string;
  /** A datatype, or a comma-separated union of them. */
  readonly type: string;
  readonly label?: string;
  /** Inputs only: the node still runs without a link here. */
  readonly optional?: boolean;
  /**
   * What to connect, in the operator's words. A required input that is empty shows a red badge
   * and one line of text; "input deliverable is not connected" says what is wrong and not what
   * to do about it, which is the difference this carries.
   */
  readonly hint?: string;
}

export type NodeExecutor = "deterministic" | "ai" | "human" | "hybrid";

export interface NodeDefinition {
  readonly type: string;
  readonly title: string;
  /** Library grouping, e.g. "research", "image", "output". */
  readonly category: string;
  /** One honest line about what the node does. */
  readonly summary: string;
  /**
   * The longer answer, for the panel: what this node reads, what it writes, and what it decides.
   * A node whose slots are typed `QC` and whose summary is four words is not self-explanatory to
   * anyone who did not write the pipeline, and the panel is where an operator goes to find out.
   */
  readonly help?: string;
  readonly inputs: readonly SlotSpec[];
  readonly outputs: readonly SlotSpec[];
  readonly widgets: readonly WidgetSpec[];
  /**
   * Groups of inputs where at least one must be connected, though no single one is required.
   *
   * Some nodes take the same thing in two shapes: Compose Video needs a picture, and a picture is
   * either a frame sequence or a clip. Marking both slots required is a lie a lane then has to
   * apologise for in its caveat; marking both optional says a node with no picture at all is
   * fine. This says what is actually true, and the message names the alternatives.
   */
  readonly requires_one_of?: readonly (readonly string[])[];
  /** Header tint. Defaults to the category colour the app's stylesheet assigns. */
  readonly accent?: string;
  readonly executor?: NodeExecutor;
  /** The pipeline stage this node stands for, when it stands for one. */
  readonly stage?: string;
  /** Free-text nodes: no slots, body is one big note. */
  readonly kind?: "node" | "note";
  /** Default body width in canvas units. */
  readonly width?: number;
  /** Search terms beyond the title and summary. */
  readonly keywords?: readonly string[];
}

export interface NodeCatalog {
  get(type: string): NodeDefinition | undefined;
  all(): readonly NodeDefinition[];
  categories(): readonly string[];
  /** Ranked search over title, type, summary and keywords. Empty query returns everything. */
  search(query: string, limit?: number): readonly NodeDefinition[];
}

export const DEFAULT_NODE_WIDTH = 240;
export const MIN_NODE_WIDTH = 140;
export const MAX_NODE_WIDTH = 720;

/** Litegraph's geometry, which is what makes a node graph feel like one. */
export const NODE_TITLE_HEIGHT = 30;
export const NODE_SLOT_HEIGHT = 20;
export const NODE_WIDGET_HEIGHT = 20;

/**
 * A size estimate for a node before (or without) DOM measurement — the canvas shows nodes with
 * it until ResizeObserver reports truth, and thumbnails never measure at all.
 */
export function estimateNodeSize(
  node: { readonly width: number | null; readonly collapsed: boolean },
  def: NodeDefinition | null,
): { width: number; height: number } {
  const width = node.width ?? def?.width ?? DEFAULT_NODE_WIDTH;
  if (node.collapsed || !def) return { width, height: NODE_TITLE_HEIGHT + 2 };
  const slotRows = Math.max(def.inputs.length, def.outputs.length);
  let height = NODE_TITLE_HEIGHT + 14 + slotRows * NODE_SLOT_HEIGHT + 24;
  for (const widget of def.widgets) {
    if (widget.kind === "textarea") height += 90;
    else if (widget.kind === "chips") {
      // Label row plus the pill grid, three to a row at the default node width.
      height += NODE_WIDGET_HEIGHT + 6 + Math.ceil((widget.options?.length ?? 0) / 3) * 24;
    } else height += NODE_WIDGET_HEIGHT + 4;
  }
  return { width, height };
}

/**
 * A multi-select widget's value: one comma-separated string, not an array.
 *
 * `WidgetValue` is a string, a number or a boolean in every layer this crosses — the graph
 * document, the Pydantic contract, the DAG node's `params`, the stage's `_param_list` reader —
 * and widening all of them to carry a list would be a contract change in five places for a
 * control that reads back as `"bluesky,mastodon"` either way. So the list lives in the string,
 * and these two functions are the only place that knows it.
 */
export function parseChips(value: WidgetValue): string[] {
  return String(value)
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

export function formatChips(values: readonly string[], options?: readonly string[]): string {
  const unique = [...new Set(values.filter((v) => v !== ""))];
  // Kept in the definition's own order so the same selection always serialises identically,
  // whatever order the operator clicked in.
  const ordered = options ? options.filter((o) => unique.includes(o)) : unique;
  const extras = unique.filter((v) => !ordered.includes(v));
  return [...ordered, ...extras].join(",");
}

export function widgetDefaults(def: NodeDefinition): Record<string, WidgetValue> {
  const values: Record<string, WidgetValue> = {};
  for (const widget of def.widgets) values[widget.name] = widget.default;
  return values;
}

export function findSlot(slots: readonly SlotSpec[], name: string): SlotSpec | undefined {
  return slots.find((s) => s.name === name);
}

function score(def: NodeDefinition, needle: string): number {
  const title = def.title.toLowerCase();
  const type = def.type.toLowerCase();
  if (title === needle || type === needle) return 0;
  if (title.startsWith(needle) || type.startsWith(needle)) return 1;
  if (title.includes(needle) || type.includes(needle)) return 2;
  if (def.category.toLowerCase().includes(needle)) return 3;
  if (def.summary.toLowerCase().includes(needle)) return 4;
  if ((def.keywords ?? []).some((k) => k.toLowerCase().includes(needle))) return 5;
  return Number.POSITIVE_INFINITY;
}

/** Build a catalogue from a list of definitions. Duplicate types are a programming error. */
export function createCatalog(definitions: readonly NodeDefinition[]): NodeCatalog {
  const byType = new Map<string, NodeDefinition>();
  for (const def of definitions) {
    if (byType.has(def.type)) throw new Error(`duplicate node type ${def.type}`);
    byType.set(def.type, def);
  }
  const categories = [...new Set(definitions.map((d) => d.category))];
  return {
    get: (type) => byType.get(type),
    all: () => definitions,
    categories: () => categories,
    search: (query, limit = 50) => {
      const needle = query.trim().toLowerCase();
      if (needle === "") return definitions.slice(0, limit);
      return definitions
        .map((def) => ({ def, rank: score(def, needle) }))
        .filter((entry) => Number.isFinite(entry.rank))
        .sort((a, b) => a.rank - b.rank || a.def.title.localeCompare(b.def.title))
        .slice(0, limit)
        .map((entry) => entry.def);
    },
  };
}
