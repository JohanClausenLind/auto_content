/**
 * Slot datatypes and their dot colours.
 *
 * A link may only join slots whose datatypes are compatible, which is what keeps a graph
 * meaningful: a VIDEO output cannot feed a SCRIPT input. `ANY` is the wildcard, and a slot may
 * declare a union ("IMAGE,SEQUENCE") the same way ComfyUI does.
 *
 * The palette follows ComfyUI's "Dark (Default)" node_slot colours so the canvas reads the way a
 * node graph is expected to read; the type names are ours.
 */

export const WILDCARD = "ANY";

/** Datatype -> dot colour. Types not listed here fall back to `DEFAULT_SLOT_COLOR`. */
export const SLOT_COLORS: Readonly<Record<string, string>> = {
  ANY: "#AAAAAA",
  BRIEF: "#FFA931",
  SOURCES: "#A8DADC",
  EVIDENCE: "#6EE7B7",
  CLAIMS: "#81C784",
  DATASET: "#CDFFCD",
  STORY: "#B39DDB",
  SCRIPT: "#FFD500",
  TEXT: "#DCC274",
  ARTBOARD: "#C2FFAE",
  IMAGE: "#64B5F6",
  SEQUENCE: "#FF9CF9",
  SHOTS: "#F9A825",
  CONTROLS: "#8D6E63",
  MASK: "#9FA8DA",
  VIDEO: "#FF6E6E",
  AUDIO: "#66FFFF",
  CAPTIONS: "#ECB4B4",
  TIMELINE: "#B0B0B0",
  PERSONA: "#F48FB1",
  PACKAGE: "#AD7452",
  QC: "#E57373",
  DELIVERABLE: "#4DB6AC",
};

export const DEFAULT_SLOT_COLOR = "#AAAAAA";

export function slotColor(type: string): string {
  const first = splitTypes(type)[0] ?? WILDCARD;
  return SLOT_COLORS[first] ?? DEFAULT_SLOT_COLOR;
}

/** Every colour a multi-type slot should show, capped like ComfyUI caps its dot slices. */
export const MAX_SLOT_COLOR_SLICES = 3;

export function slotColors(type: string): string[] {
  return splitTypes(type)
    .slice(0, MAX_SLOT_COLOR_SLICES)
    .map((t) => SLOT_COLORS[t] ?? DEFAULT_SLOT_COLOR);
}

export function splitTypes(type: string): string[] {
  return type
    .split(",")
    .map((t) => t.trim().toUpperCase())
    .filter((t) => t.length > 0);
}

/** True when a link from an output of `sourceType` may enter an input of `targetType`. */
export function typesCompatible(sourceType: string, targetType: string): boolean {
  const source = splitTypes(sourceType);
  const target = splitTypes(targetType);
  if (source.length === 0 || target.length === 0) return false;
  if (source.includes(WILDCARD) || target.includes(WILDCARD)) return true;
  return source.some((s) => target.includes(s));
}
