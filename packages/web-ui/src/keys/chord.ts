/**
 * Chords and sequences.
 *
 * A *chord* is one keypress, normalised to a string: `"Mod+k"`, `"g"`, `"?"`, `"Shift+Escape"`.
 * A *binding* is a sequence of one or two chords, so Gmail-style `g` then `i` is `["g", "i"]`.
 *
 * `Mod` is the platform's command key — ⌘ on Apple hardware, Ctrl everywhere else — stored as one
 * token so a keymap moved between machines keeps working. Shift is only recorded for keys that do
 * not already carry it in their printed form: the browser reports `?` for Shift+/, so the chord is
 * `"?"` and never `"Shift+?"`, which would be unmatchable.
 */

export const MAX_CHORDS_PER_BINDING = 2;

/** Modifier keys are never a chord on their own. */
const MODIFIER_KEYS = new Set(["Shift", "Control", "Alt", "Meta", "AltGraph", "CapsLock"]);

/** Printed names for keys whose `event.key` is unfriendly. */
const KEY_LABELS: Record<string, string> = {
  " ": "Space",
  ArrowUp: "↑",
  ArrowDown: "↓",
  ArrowLeft: "←",
  ArrowRight: "→",
  Escape: "Esc",
  Enter: "↵",
  Backspace: "⌫",
  Delete: "Del",
  Tab: "⇥",
};

export function isApplePlatform(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || "");
}

/**
 * Normalise a keyboard event to a chord, or null when there is nothing bindable (a bare modifier,
 * or a composition still in progress).
 */
export function eventToChord(event: KeyboardEvent): string | null {
  const key = event.key;
  if (!key || MODIFIER_KEYS.has(key) || event.isComposing) return null;

  const parts: string[] = [];
  if (event.metaKey || event.ctrlKey) parts.push("Mod");
  if (event.altKey) parts.push("Alt");
  // A printable key already reflects Shift in its value ("?" not "/"), so recording Shift as well
  // would produce a chord no event can ever match.
  if (event.shiftKey && key.length > 1) parts.push("Shift");

  parts.push(key.length === 1 ? key.toLowerCase() : key);
  return parts.join("+");
}

/** True when the chord needs a modifier, and so is safe to capture while a field has focus. */
export function chordHasModifier(chord: string): boolean {
  return chord.startsWith("Mod+") || chord.startsWith("Alt+") || chord.includes("+Alt+");
}

/** Human-readable form of one chord, e.g. `"Mod+k"` → `"⌘K"` on Apple, `"Ctrl+K"` elsewhere. */
export function formatChord(chord: string, apple: boolean = isApplePlatform()): string {
  const parts = chord.split("+");
  const key = parts[parts.length - 1] ?? "";
  const mods = parts.slice(0, -1);
  const out: string[] = [];
  for (const mod of mods) {
    if (mod === "Mod") out.push(apple ? "⌘" : "Ctrl");
    else if (mod === "Alt") out.push(apple ? "⌥" : "Alt");
    else if (mod === "Shift") out.push(apple ? "⇧" : "Shift");
  }
  const label = KEY_LABELS[key] ?? (key.length === 1 ? key.toUpperCase() : key);
  // Apple keyboards print modifiers glyph-adjacent; everywhere else they are joined with +.
  return apple ? out.join("") + label : [...out, label].join("+");
}

/** Human-readable form of a whole binding: `["g","i"]` → `"G then I"`. */
export function formatBinding(binding: readonly string[] | null, apple: boolean = isApplePlatform()): string {
  if (!binding || binding.length === 0) return "—";
  return binding.map((c) => formatChord(c, apple)).join(" then ");
}

/** Two bindings are equal when their chord sequences match exactly. */
export function bindingsEqual(a: readonly string[] | null, b: readonly string[] | null): boolean {
  if (a === null || b === null) return a === b;
  return a.length === b.length && a.every((chord, i) => chord === b[i]);
}

/**
 * Does `prefix` start `binding`? A shorter binding that prefixes a longer one shadows it — `g`
 * would fire before `g i` could ever complete — so the editor reports that as a conflict.
 */
export function isPrefixOf(prefix: readonly string[], binding: readonly string[]): boolean {
  if (prefix.length >= binding.length) return false;
  return prefix.every((chord, i) => chord === binding[i]);
}

/**
 * Input types that are controls rather than text entry. Focus sitting on a checkbox or a slider is
 * not typing, so shortcuts must still fire there — only text fields get to swallow a bare letter.
 */
const NON_TEXT_INPUT_TYPES = new Set([
  "checkbox",
  "radio",
  "button",
  "submit",
  "reset",
  "image",
  "range",
  "color",
  "file",
]);

/**
 * True when the keyboard is being used for text entry right now.
 *
 * `event.target` alone is not enough: react-aria's Autocomplete (the command palette) handles keys
 * on a wrapper and the event that reaches `window` carries that div as its target, not the focused
 * search field. Consulting the active element as well is what stops typing "create" in the palette
 * from firing the shortcuts bound to c, r and e.
 */
export function isTypingContext(event: KeyboardEvent): boolean {
  if (isEditableTarget(event.target)) return true;
  return typeof document !== "undefined" && isEditableTarget(document.activeElement);
}

/** True when focus is somewhere typing should win over shortcuts. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!target || !(target instanceof Element)) return false;
  const el = target as HTMLElement;
  const tag = el.tagName;
  if (tag === "INPUT") return !NON_TEXT_INPUT_TYPES.has((el as HTMLInputElement).type);
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return el.closest("[contenteditable='true']") !== null;
}
