import { ActionCenterBadge, Button, CommandPalette, Dialog, KeymapProvider, SequenceHint, ShortcutSheet, ThemeCustomizer, useTheme } from "@content-factory/web-ui";
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { actionItemsQuery, useLogout, useMeta, useSession, useSwitchWorkspace } from "../api/queries";
import type { Session } from "../api/types";
import { usePrefs } from "../prefs/PrefsProvider";
import { keymapSyncAdapter } from "../prefs/adapter";
import { AssistantPanel } from "./AssistantPanel";
import { buildCommands } from "./commands";
import { buildKeyActions } from "./keymap";
import { NAV_SECTIONS, visibleAreas } from "./nav";
import { UserMenu } from "./UserMenu";
import { WorkspaceSwitcher } from "./WorkspaceSwitcher";

export function Shell() {
  const { data: session } = useSession();
  if (!session) return null; // the route guard has already redirected
  return <ShellInner session={session} />;
}

function ShellInner({ session }: { session: Session }) {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const theme = useTheme();
  const { prefs, set: setPref } = usePrefs();
  const { data: meta } = useMeta();
  const switchWs = useSwitchWorkspace();
  const logout = useLogout();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [customizerOpen, setCustomizerOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const { data: actionItems } = useQuery(actionItemsQuery);
  const actionTone = actionItems?.some((i) => i.severity === "critical") ? "danger" : actionItems?.some((i) => i.severity === "warning") ? "warn" : "neutral";

  const areas = useMemo(() => visibleAreas(session.account.is_owner), [session.account.is_owner]);
  const go = (to: string) => void navigate({ to });

  const signOut = () => {
    logout.mutate(undefined, { onSettled: () => void navigate({ to: "/login" }) });
  };

  const commands = useMemo(
    () =>
      buildCommands({
        areas,
        navigate: go,
        toggleScheme: theme.toggleScheme,
        openCustomizer: () => setCustomizerOpen(true),
        openShortcuts: () => setShortcutsOpen(true),
        signOut,
        switchWorkspace: (id) => switchWs.mutate(id),
        workspaces: session.workspaces,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [areas, session.workspaces, theme.toggleScheme],
  );

  const keyActions = useMemo(
    () =>
      buildKeyActions({
        areas,
        navigate: go,
        togglePalette: () => setPaletteOpen((o) => !o),
        openShortcuts: () => setShortcutsOpen(true),
        openAssistant: () => setAssistantOpen(true),
        toggleScheme: theme.toggleScheme,
        toggleNav: () => setPref("navCollapsed", !prefs.navCollapsed),
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [areas, theme.toggleScheme, prefs.navCollapsed],
  );

  const keymapSync = useMemo(() => (session.account.id ? keymapSyncAdapter : null), [session.account.id]);

  // Close the mobile nav on navigation.
  useEffect(() => setNavOpen(false), [pathname]);

  const schemeLabel = theme.resolved.scheme === "dark" ? "Switch to light theme" : "Switch to dark theme";

  return (
    <KeymapProvider actions={keyActions} sync={keymapSync}>
      <div className="cf-shell" data-nav-open={navOpen || undefined} data-rail={prefs.navCollapsed ? "icons" : undefined}>
        <a className="cf-skip" href="#cf-main">
          Skip to content
        </a>
        <header className="cf-topbar">
          <div className="cf-topbar__left">
            <Button variant="ghost" className="cf-nav-toggle" aria-label={navOpen ? "Hide navigation" : "Show navigation"} aria-expanded={navOpen} aria-controls="cf-sidenav" onPress={() => setNavOpen((o) => !o)}>
              <span aria-hidden="true">☰</span>
            </Button>
            <Link to="/" className="cf-brand-link" aria-label="Content Factory home">
              <span className="cf-brand" aria-hidden="true">F</span>
              <span className="cf-brand-name">Content Factory</span>
            </Link>
            <WorkspaceSwitcher workspaces={session.workspaces} currentId={session.current_workspace_id} onSwitch={(id) => switchWs.mutate(id)} busy={switchWs.isPending} />
            {meta?.kill_switch && (
              <span className="cf-pill cf-pill--danger" role="status">
                Kill switch ON · distribution disabled
              </span>
            )}
          </div>
          <div className="cf-topbar__right">
            <Button variant="primary" size="sm" onPress={() => go("/create")}>
              <span aria-hidden="true">+</span> Create
            </Button>
            <Button variant="ghost" size="sm" className="cf-palette-trigger" aria-label="Open command palette" aria-keyshortcuts="Control+K Meta+K" onPress={() => setPaletteOpen(true)}>
              <span aria-hidden="true">Search</span>
              <kbd className="cf-kbd" aria-hidden="true">⌘K</kbd>
            </Button>
            <span className="cf-budget" title="Budget tracking arrives with the pipeline">
              Budget <span aria-hidden="true">—</span>
              <span className="cf-visually-hidden">not tracked yet</span>
            </span>
            <Button variant="ghost" size="sm" onPress={() => setAssistantOpen(true)}>
              Assistant
            </Button>
            <ActionCenterBadge count={actionItems?.length ?? 0} tone={actionTone} onPress={() => go("/")} />
            <Button variant="ghost" size="sm" aria-label={schemeLabel} aria-pressed={theme.resolved.scheme === "dark"} onPress={theme.toggleScheme}>
              <span aria-hidden="true">{theme.resolved.scheme === "dark" ? "☾" : "☀"}</span>
            </Button>
            <UserMenu account={session.account} meta={meta} onSettings={() => go("/settings")} onCustomizeTheme={() => setCustomizerOpen(true)} onSignOut={signOut} />
          </div>
        </header>

        <nav id="cf-sidenav" className="cf-sidenav" aria-label="Areas">
          {NAV_SECTIONS.map((section) => {
            const items = areas.filter((a) => a.section === section.id);
            if (items.length === 0) return null;
            return (
              <div key={section.id} className="cf-sidenav__section">
                <h2 className="cf-visually-hidden">{section.label}</h2>
                <ul className="cf-sidenav__list">
                  {items.map((a) => (
                    <li key={a.to}>
                      <Link to={a.to} className="cf-sidenav__link" title={a.label} activeProps={{ className: "cf-sidenav__link cf-sidenav__link--active", "aria-current": "page" }} activeOptions={{ exact: a.to === "/" }}>
                        <span className="cf-sidenav__icon" aria-hidden="true">{a.icon}</span>
                        <span className="cf-sidenav__label">{a.label}</span>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </nav>

        <main id="cf-main" className={pathname.startsWith("/workspace") ? "cf-main cf-main--flush" : "cf-main"} tabIndex={-1}>
          <Outlet />
        </main>

        <SequenceHint />
        <CommandPalette commands={commands} isOpen={paletteOpen} onOpenChange={setPaletteOpen} />
        <ShortcutSheet
          isOpen={shortcutsOpen}
          onOpenChange={setShortcutsOpen}
          footer={
            <Button
              variant="ghost"
              size="sm"
              onPress={() => {
                setShortcutsOpen(false);
                void navigate({ to: "/settings", search: { tab: "keyboard" } });
              }}
            >
              Change these in Settings ▸ Keyboard
            </Button>
          }
        />
        <AssistantPanel isOpen={assistantOpen} onOpenChange={setAssistantOpen} />
        <Dialog title="Customize theme" description="Changes apply immediately and are saved to your account." size="lg" isOpen={customizerOpen} onOpenChange={setCustomizerOpen}>
          <ThemeCustomizer />
        </Dialog>
      </div>
    </KeymapProvider>
  );
}
