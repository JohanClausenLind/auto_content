export interface NavArea {
  to: string;
  label: string;
  /** Rail glyph (text icon; the rail shows icon over a small label). */
  icon: string;
  /** Extra words the palette should match. */
  keywords?: string;
  /**
   * Second chord of this area's `g` sequence. Every area needs one and they must be unique —
   * `nav.test.ts` fails otherwise, so a new area cannot quietly ship without a shortcut.
   */
  key: string;
  ownerOnly?: boolean;
  section: "work" | "library" | "system";
}

export const NAV_AREAS: readonly NavArea[] = [
  { to: "/", label: "Home", icon: "⌂", section: "work", keywords: "dashboard overview", key: "h" },
  { to: "/workspace", label: "Workspace", icon: "◫", section: "work", keywords: "canvas node graph editor nodes", key: "w" },
  { to: "/inbox", label: "Inbox", icon: "▤", section: "work", keywords: "approvals tasks", key: "i" },
  { to: "/calendar", label: "Calendar", icon: "▦", section: "work", keywords: "schedule", key: "c" },
  { to: "/projects", label: "Projects", icon: "▶", section: "work", keywords: "campaigns runs", key: "p" },
  { to: "/requests", label: "Requests", icon: "✉", section: "work", keywords: "portal briefs clients", key: "r" },
  { to: "/personas", label: "Personas", icon: "☺", section: "library", keywords: "voice", key: "e" },
  { to: "/models", label: "Models", icon: "◈", section: "library", keywords: "weights checkpoints install download missing store huggingface", key: "m" },
  { to: "/assets", label: "Assets", icon: "▣", section: "library", keywords: "media images video", key: "a" },
  { to: "/brand", label: "Brand", icon: "◮", section: "library", keywords: "colours fonts logo", key: "b" },
  { to: "/sources", label: "Sources", icon: "❧", section: "library", keywords: "research feeds", key: "s" },
  { to: "/templates", label: "Templates", icon: "▥", section: "library", keywords: "layouts", key: "t" },
  { to: "/connections", label: "Connections", icon: "⇄", section: "system", keywords: "accounts channels", key: "n" },
  { to: "/analytics", label: "Analytics", icon: "∿", section: "system", keywords: "stats performance", key: "y" },
  { to: "/operations", label: "Operations", icon: "⚙", section: "system", keywords: "workers kill switch budget", ownerOnly: true, key: "o" },
  { to: "/settings", label: "Settings", icon: "✦", section: "system", keywords: "preferences theme", key: "," },
];

export const NAV_SECTIONS: { id: NavArea["section"]; label: string }[] = [
  { id: "work", label: "Work" },
  { id: "library", label: "Library" },
  { id: "system", label: "System" },
];

export function visibleAreas(isOwner: boolean): NavArea[] {
  return NAV_AREAS.filter((a) => !a.ownerOnly || isOwner);
}
