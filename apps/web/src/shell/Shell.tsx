import { ActionCenterBadge, Button, CommandPalette, Dialog, ThemeCustomizer, useCommandPaletteHotkey, useTheme } from "@content-factory/web-ui";
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import { useLogout, useMeta, useSession, useSwitchWorkspace } from "../api/queries";
import type { Session } from "../api/types";
import { buildCommands } from "./commands";
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
  const { data: meta } = useMeta();
  const switchWs = useSwitchWorkspace();
  const logout = useLogout();
  const [paletteOpen, setPaletteOpen] = useCommandPaletteHotkey();
  const [customizerOpen, setCustomizerOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);

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
        signOut,
        switchWorkspace: (id) => switchWs.mutate(id),
        workspaces: session.workspaces,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [areas, session.workspaces, theme.toggleScheme],
  );

  // Close the mobile nav on navigation.
  useEffect(() => setNavOpen(false), [pathname]);

  const schemeLabel = theme.resolved.scheme === "dark" ? "Switch to light theme" : "Switch to dark theme";

  return (
    <div className="cf-shell" data-nav-open={navOpen || undefined}>
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
          <ActionCenterBadge count={0} onPress={() => go("/inbox")} />
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
              <h2 className="cf-sidenav__heading">{section.label}</h2>
              <ul className="cf-sidenav__list">
                {items.map((a) => (
                  <li key={a.to}>
                    <Link to={a.to} className="cf-sidenav__link" activeProps={{ className: "cf-sidenav__link cf-sidenav__link--active", "aria-current": "page" }} activeOptions={{ exact: a.to === "/" }}>
                      {a.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </nav>

      <main id="cf-main" className="cf-main" tabIndex={-1}>
        <Outlet />
      </main>

      <CommandPalette commands={commands} isOpen={paletteOpen} onOpenChange={setPaletteOpen} />
      <Dialog title="Customize theme" description="Changes apply immediately and are saved to your account." size="lg" isOpen={customizerOpen} onOpenChange={setCustomizerOpen}>
        <ThemeCustomizer />
      </Dialog>
    </div>
  );
}
