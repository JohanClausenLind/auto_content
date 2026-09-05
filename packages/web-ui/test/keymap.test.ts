import { describe, expect, it } from "vitest";
import {
  bindingsEqual,
  chordHasModifier,
  eventToChord,
  findConflicts,
  formatBinding,
  formatChord,
  isPrefixOf,
  isEditableTarget,
  isTypingContext,
  matchSequence,
  parseKeymapState,
  resolveKeymap,
  type KeyAction,
  type KeymapState,
} from "../src/index";

const key = (init: KeyboardEventInit) => new KeyboardEvent("keydown", init);

function action(id: string, defaultBinding: string[] | null, extra: Partial<KeyAction> = {}): KeyAction {
  return { id, title: id, group: "Test", defaultBinding, run: () => undefined, ...extra };
}

describe("eventToChord", () => {
  it("lowercases printable keys so Shift+T and t are the same chord", () => {
    expect(eventToChord(key({ key: "t" }))).toBe("t");
    // The browser reports the shifted character, and the chord must not also carry Shift or it
    // could never match a real keypress.
    expect(eventToChord(key({ key: "T", shiftKey: true }))).toBe("t");
    expect(eventToChord(key({ key: "?", shiftKey: true }))).toBe("?");
  });

  it("folds Ctrl and Meta into one Mod token so a keymap survives moving machines", () => {
    expect(eventToChord(key({ key: "k", ctrlKey: true }))).toBe("Mod+k");
    expect(eventToChord(key({ key: "k", metaKey: true }))).toBe("Mod+k");
  });

  it("keeps Shift for non-printable keys, where it is not already in the value", () => {
    expect(eventToChord(key({ key: "Escape", shiftKey: true }))).toBe("Shift+Escape");
    expect(eventToChord(key({ key: "ArrowUp" }))).toBe("ArrowUp");
  });

  it("returns null for bare modifiers and in-flight compositions", () => {
    expect(eventToChord(key({ key: "Shift", shiftKey: true }))).toBeNull();
    expect(eventToChord(key({ key: "Control", ctrlKey: true }))).toBeNull();
    expect(eventToChord(key({ key: "a", isComposing: true }))).toBeNull();
  });

  it("orders modifiers so the same keypress always produces the same string", () => {
    expect(eventToChord(key({ key: "Enter", ctrlKey: true, altKey: true, shiftKey: true }))).toBe("Mod+Alt+Shift+Enter");
  });
});

describe("formatting", () => {
  it("prints Apple glyphs on Apple and words elsewhere", () => {
    expect(formatChord("Mod+k", true)).toBe("⌘K");
    expect(formatChord("Mod+k", false)).toBe("Ctrl+K");
    expect(formatChord("ArrowUp", false)).toBe("↑");
  });

  it("joins a sequence with 'then', and shows unbound as a dash", () => {
    expect(formatBinding(["g", "i"], false)).toBe("G then I");
    expect(formatBinding(null, false)).toBe("—");
  });
});

describe("chordHasModifier", () => {
  it("is true only for chords that cannot be ordinary typing", () => {
    expect(chordHasModifier("Mod+k")).toBe(true);
    expect(chordHasModifier("Alt+Enter")).toBe(true);
    expect(chordHasModifier("c")).toBe(false);
    expect(chordHasModifier("?")).toBe(false);
  });
});

describe("bindings", () => {
  it("compares and detects prefixes", () => {
    expect(bindingsEqual(["g", "i"], ["g", "i"])).toBe(true);
    expect(bindingsEqual(["g", "i"], ["g", "p"])).toBe(false);
    expect(bindingsEqual(null, null)).toBe(true);
    expect(bindingsEqual(null, ["g"])).toBe(false);
    expect(isPrefixOf(["g"], ["g", "i"])).toBe(true);
    expect(isPrefixOf(["g", "i"], ["g", "i"])).toBe(false);
    expect(isPrefixOf(["h"], ["g", "i"])).toBe(false);
  });
});

describe("resolveKeymap", () => {
  const actions = [action("a", ["c"]), action("b", ["g", "i"]), action("locked", ["Mod+k"], { locked: true })];

  it("uses defaults when nothing is overridden", () => {
    const resolved = resolveKeymap(actions, { version: 1, overrides: {} });
    expect(resolved.map((r) => r.binding)).toEqual([["c"], ["g", "i"], ["Mod+k"]]);
    expect(resolved.every((r) => !r.custom)).toBe(true);
  });

  it("applies an override and marks it custom", () => {
    const resolved = resolveKeymap(actions, { version: 1, overrides: { a: ["x"] } });
    expect(resolved[0]?.binding).toEqual(["x"]);
    expect(resolved[0]?.custom).toBe(true);
    expect(resolved[1]?.custom).toBe(false);
  });

  it("treats an explicit null as deliberately unbound, distinct from absent", () => {
    const resolved = resolveKeymap(actions, { version: 1, overrides: { a: null } });
    expect(resolved[0]?.binding).toBeNull();
    expect(resolved[0]?.custom).toBe(true);
  });

  it("ignores overrides aimed at a locked action", () => {
    const resolved = resolveKeymap(actions, { version: 1, overrides: { locked: ["z"] } });
    expect(resolved[2]?.binding).toEqual(["Mod+k"]);
    expect(resolved[2]?.custom).toBe(false);
  });

  it("picks up a changed default rather than freezing what was stored", () => {
    // Only overrides are persisted, so shipping a new default reaches an existing account.
    const stored: KeymapState = { version: 1, overrides: { a: ["x"] } };
    const shipped = [action("a", ["c"]), action("b", ["g", "n"])];
    expect(resolveKeymap(shipped, stored)[1]?.binding).toEqual(["g", "n"]);
  });
});

describe("findConflicts", () => {
  it("reports the same sequence bound twice", () => {
    const resolved = resolveKeymap([action("a", ["c"]), action("b", ["c"])], { version: 1, overrides: {} });
    expect(findConflicts(resolved)).toEqual([{ actionId: "a", otherId: "b", kind: "duplicate" }]);
  });

  it("reports a short binding that would shadow a longer one starting with it", () => {
    const resolved = resolveKeymap([action("a", ["g"]), action("b", ["g", "i"])], { version: 1, overrides: {} });
    expect(findConflicts(resolved)).toEqual([{ actionId: "a", otherId: "b", kind: "shadow" }]);
  });

  it("says nothing about unbound actions or distinct bindings", () => {
    const resolved = resolveKeymap([action("a", null), action("b", null), action("c", ["x"])], { version: 1, overrides: {} });
    expect(findConflicts(resolved)).toEqual([]);
  });
});

describe("matchSequence", () => {
  const resolved = resolveKeymap([action("create", ["c"]), action("inbox", ["g", "i"])], { version: 1, overrides: {} });

  it("fires on an exact single chord", () => {
    expect(matchSequence(resolved, ["c"]).exact?.action.id).toBe("create");
  });

  it("waits on a prefix rather than firing", () => {
    const m = matchSequence(resolved, ["g"]);
    expect(m.exact).toBeNull();
    expect(m.partial).toBe(true);
  });

  it("completes a two-chord sequence", () => {
    expect(matchSequence(resolved, ["g", "i"]).exact?.action.id).toBe("inbox");
  });

  it("matches nothing for an unknown sequence", () => {
    expect(matchSequence(resolved, ["g", "z"])).toEqual({ exact: null, partial: false });
  });
});

describe("parseKeymapState", () => {
  it("accepts a well-formed state", () => {
    expect(parseKeymapState({ version: 1, overrides: { a: ["g", "i"], b: null } })).toEqual({
      version: 1,
      overrides: { a: ["g", "i"], b: null },
    });
  });

  it("rejects junk rather than letting it reach the dispatcher", () => {
    expect(parseKeymapState(null)).toBeNull();
    expect(parseKeymapState({ version: 2, overrides: {} })).toBeNull();
    expect(parseKeymapState({ version: 1, overrides: { a: "c" } })).toBeNull();
    expect(parseKeymapState({ version: 1, overrides: { a: ["g", "i", "x"] } })).toBeNull();
    expect(parseKeymapState({ version: 1, overrides: { a: [] } })).toBeNull();
  });
});

describe("isTypingContext", () => {
  it("treats text fields as typing and plain controls as not", () => {
    const text = document.createElement("input");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    const range = document.createElement("input");
    range.type = "range";
    const area = document.createElement("textarea");
    const div = document.createElement("div");

    expect(isEditableTarget(text)).toBe(true);
    expect(isEditableTarget(area)).toBe(true);
    // A focused switch or slider is not text entry, so shortcuts must still reach the app.
    expect(isEditableTarget(checkbox)).toBe(false);
    expect(isEditableTarget(range)).toBe(false);
    expect(isEditableTarget(div)).toBe(false);
    expect(isEditableTarget(null)).toBe(false);
  });

  it("sees through an event re-dispatched from a wrapper element", () => {
    // react-aria's Autocomplete handles keys on a container, so the event reaching window carries
    // that div as its target while the search field still holds focus.
    const field = document.createElement("input");
    const wrapper = document.createElement("div");
    document.body.append(wrapper, field);
    field.focus();
    try {
      const event = new KeyboardEvent("keydown", { key: "c" });
      Object.defineProperty(event, "target", { value: wrapper });
      expect(isEditableTarget(event.target)).toBe(false);
      expect(isTypingContext(event)).toBe(true);
    } finally {
      wrapper.remove();
      field.remove();
    }
  });

  it("is false when nothing focusable holds the caret", () => {
    document.body.focus();
    const event = new KeyboardEvent("keydown", { key: "c" });
    expect(isTypingContext(event)).toBe(false);
  });
});
