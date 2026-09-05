/**
 * Stored keymap state.
 *
 * Only *overrides* are persisted, never the whole table. A binding the operator never touched
 * follows the application default, so shipping a new action — or changing a default — reaches
 * existing accounts instead of being frozen by a snapshot taken on first visit. An explicit `null`
 * means "unbound on purpose" and is distinct from "absent, use the default".
 */

import { z } from "zod";
import { MAX_CHORDS_PER_BINDING } from "./chord";

export const KEYMAP_VERSION = 1;

/** Chords are produced by `eventToChord`, so the shape is `[Mod+][Alt+][Shift+]key`. */
export const chordSchema = z
  .string()
  .min(1)
  .max(40)
  .regex(/^(Mod\+)?(Alt\+)?(Shift\+)?[^+\s]+$/, "Expected a chord like 'Mod+k', 'g' or '?'");

export const bindingSchema = z.array(chordSchema).min(1).max(MAX_CHORDS_PER_BINDING);

export const keymapStateSchema = z.object({
  version: z.literal(KEYMAP_VERSION),
  /** action id → binding, or null for deliberately unbound. Absent means "use the default". */
  overrides: z.record(z.string().min(1).max(64), bindingSchema.nullable()),
});

export type Binding = z.infer<typeof bindingSchema>;
export type KeymapState = z.infer<typeof keymapStateSchema>;

export const DEFAULT_KEYMAP_STATE: KeymapState = { version: KEYMAP_VERSION, overrides: {} };

/** Parse untrusted JSON into state, or null when it does not fit the schema. */
export function parseKeymapState(value: unknown): KeymapState | null {
  const parsed = keymapStateSchema.safeParse(value);
  return parsed.success ? parsed.data : null;
}
