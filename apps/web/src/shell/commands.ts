import type { Command } from "@content-factory/web-ui";
import type { NavArea } from "./nav";

export interface CommandDeps {
  areas: readonly NavArea[];
  navigate: (to: string) => void;
  toggleScheme: () => void;
  openCustomizer: () => void;
  signOut: () => void;
  switchWorkspace: (id: string) => void;
  workspaces: readonly { id: string; name: string }[];
}

export function buildCommands(d: CommandDeps): Command[] {
  const nav: Command[] = d.areas.map((a) => ({
    id: `go:${a.to}`,
    title: `Go to ${a.label}`,
    group: "Navigate",
    keywords: a.keywords ?? "",
    run: () => d.navigate(a.to),
  }));
  const ws: Command[] = d.workspaces.map((w) => ({
    id: `ws:${w.id}`,
    title: `Switch to ${w.name}`,
    group: "Workspaces",
    run: () => d.switchWorkspace(w.id),
  }));
  return [
    { id: "create", title: "Create something new", group: "Actions", shortcut: "C", run: () => d.navigate("/create") },
    ...nav,
    ...ws,
    { id: "theme:toggle", title: "Toggle light / dark theme", group: "Appearance", run: d.toggleScheme },
    { id: "theme:customize", title: "Customize theme…", group: "Appearance", run: d.openCustomizer },
    { id: "session:signout", title: "Sign out", group: "Account", run: d.signOut },
  ];
}
