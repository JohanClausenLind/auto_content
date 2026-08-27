export interface NavArea {
  to: string;
  label: string;
  /** Extra words the palette should match. */
  keywords?: string;
  ownerOnly?: boolean;
  section: "work" | "library" | "system";
}

export const NAV_AREAS: readonly NavArea[] = [
  { to: "/", label: "Home", section: "work", keywords: "dashboard overview" },
  { to: "/inbox", label: "Inbox", section: "work", keywords: "approvals tasks" },
  { to: "/calendar", label: "Calendar", section: "work", keywords: "schedule" },
  { to: "/projects", label: "Projects", section: "work", keywords: "campaigns" },
  { to: "/personas", label: "Personas", section: "library", keywords: "voice" },
  { to: "/assets", label: "Assets", section: "library", keywords: "media images video" },
  { to: "/brand", label: "Brand", section: "library", keywords: "colours fonts logo" },
  { to: "/sources", label: "Sources", section: "library", keywords: "research feeds" },
  { to: "/templates", label: "Templates", section: "library", keywords: "layouts" },
  { to: "/connections", label: "Connections", section: "system", keywords: "accounts channels" },
  { to: "/analytics", label: "Analytics", section: "system", keywords: "stats performance" },
  { to: "/operations", label: "Operations", section: "system", keywords: "workers kill switch budget", ownerOnly: true },
  { to: "/settings", label: "Settings", section: "system", keywords: "preferences theme" },
];

export const NAV_SECTIONS: { id: NavArea["section"]; label: string }[] = [
  { id: "work", label: "Work" },
  { id: "library", label: "Library" },
  { id: "system", label: "System" },
];

export function visibleAreas(isOwner: boolean): NavArea[] {
  return NAV_AREAS.filter((a) => !a.ownerOnly || isOwner);
}
