import { describe, expect, it } from "vitest";
import { NAV_AREAS } from "../src/shell/nav";
import { buildKeyActions, GO_PREFIX } from "../src/shell/keymap";
import { findConflicts, resolveKeymap, DEFAULT_KEYMAP_STATE } from "@content-factory/web-ui";

describe("nav areas", () => {
  it("gives every area a shortcut letter", () => {
    const missing = NAV_AREAS.filter((a) => !a.key);
    expect(missing.map((a) => a.to)).toEqual([]);
  });

  it("keeps those letters unique, so a new area cannot silently steal one", () => {
    const seen = new Map<string, string>();
    const clashes: string[] = [];
    for (const area of NAV_AREAS) {
      const owner = seen.get(area.key);
      if (owner) clashes.push(`${area.key}: ${owner} and ${area.to}`);
      else seen.set(area.key, area.to);
    }
    expect(clashes).toEqual([]);
  });

  it("never binds the sequence prefix itself, which would shadow every g-sequence", () => {
    expect(NAV_AREAS.some((a) => a.key === GO_PREFIX)).toBe(false);
  });
});

describe("the shipped default keymap", () => {
  const actions = buildKeyActions({
    areas: NAV_AREAS,
    navigate: () => undefined,
    togglePalette: () => undefined,
    openShortcuts: () => undefined,
    openAssistant: () => undefined,
    toggleScheme: () => undefined,
    toggleNav: () => undefined,
  });

  it("has no duplicate or shadowing bindings out of the box", () => {
    const conflicts = findConflicts(resolveKeymap(actions, DEFAULT_KEYMAP_STATE));
    expect(conflicts).toEqual([]);
  });

  it("covers every nav area with a g-sequence", () => {
    const navIds = actions.filter((a) => a.id.startsWith("go:")).map((a) => a.id);
    expect(navIds).toEqual(NAV_AREAS.map((a) => `go:${a.to}`));
    for (const area of NAV_AREAS) {
      const action = actions.find((a) => a.id === `go:${area.to}`);
      expect(action?.defaultBinding).toEqual([GO_PREFIX, area.key]);
    }
  });
});
