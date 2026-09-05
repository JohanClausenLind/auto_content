/**
 * The shell's action table. Navigation entries are derived from `NAV_AREAS` rather than listed
 * again here, so a new area arrives with its `g` sequence already working and cannot drift.
 */

import type { KeyAction } from "@content-factory/web-ui";
import type { NavArea } from "./nav";

export interface KeyActionDeps {
  areas: readonly NavArea[];
  navigate: (to: string) => void;
  togglePalette: () => void;
  openShortcuts: () => void;
  openAssistant: () => void;
  toggleScheme: () => void;
  toggleNav: () => void;
}

/** Prefix chord for "go to area" sequences. */
export const GO_PREFIX = "g";

export function buildKeyActions(d: KeyActionDeps): KeyAction[] {
  const nav: KeyAction[] = d.areas.map((area) => ({
    id: `go:${area.to}`,
    title: `Go to ${area.label}`,
    group: "Navigate",
    defaultBinding: [GO_PREFIX, area.key],
    run: () => d.navigate(area.to),
  }));

  return [
    {
      id: "palette",
      title: "Command palette",
      group: "General",
      defaultBinding: ["Mod+k"],
      // The palette is the way back to everything else; it stays where the browser conventions
      // put it rather than becoming rebindable and therefore losable.
      locked: true,
      run: d.togglePalette,
    },
    { id: "shortcuts", title: "Keyboard shortcuts", group: "General", defaultBinding: ["?"], run: d.openShortcuts },
    { id: "create", title: "Create something new", group: "General", defaultBinding: ["c"], run: () => d.navigate("/create") },
    { id: "assistant", title: "Open the assistant", group: "General", defaultBinding: ["a"], run: d.openAssistant },
    { id: "theme", title: "Toggle light / dark theme", group: "Appearance", defaultBinding: ["t"], run: d.toggleScheme },
    { id: "nav", title: "Show or hide the area rail", group: "Appearance", defaultBinding: ["["], run: d.toggleNav },
    ...nav,
  ];
}
