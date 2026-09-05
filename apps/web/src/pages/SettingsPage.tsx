import { KeymapEditor, Switch, Tab, TabList, TabPanel, Tabs, ThemeCustomizer } from "@content-factory/web-ui";
import { useId } from "react";
import { useLocale, useT } from "../i18n";
import { LOCALE_NAMES, type LocaleId } from "../i18n/messages";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useSession } from "../api/queries";
import { usePrefs } from "../prefs/PrefsProvider";
import { NAV_AREAS, visibleAreas } from "../shell/nav";
import { EmptyState, Page } from "./EmptyState";
import { PushSettings } from "./PushSettings";

export type SettingsTab = "themes" | "appearance" | "keyboard" | "account" | "workspace" | "notifications";
const TABS: SettingsTab[] = ["themes", "appearance", "keyboard", "account", "workspace", "notifications"];

export function parseSettingsSearch(search: Record<string, unknown>): { tab: SettingsTab } {
  const tab = search["tab"];
  return { tab: typeof tab === "string" && (TABS as string[]).includes(tab) ? (tab as SettingsTab) : "themes" };
}

export function SettingsPage() {
  const { tab } = useSearch({ from: "/_authed/settings" });
  const navigate = useNavigate({ from: "/settings" });
  return (
    <Page title="Settings" lead="Your preferences on this device and across the server.">
      <Tabs selectedKey={tab} onSelectionChange={(k) => void navigate({ search: { tab: k as SettingsTab } })}>
        <TabList aria-label="Settings sections">
          <Tab id="themes">Themes</Tab>
          <Tab id="appearance">Appearance</Tab>
          <Tab id="keyboard">Keyboard</Tab>
          <Tab id="account">Account</Tab>
          <Tab id="workspace">Workspace</Tab>
          <Tab id="notifications">Notifications</Tab>
        </TabList>
        <TabPanel id="themes">
          <ThemeCustomizer />
        </TabPanel>
        <TabPanel id="appearance">
          <AppearanceSettings />
        </TabPanel>
        <TabPanel id="keyboard">
          <KeymapEditor />
        </TabPanel>
        <TabPanel id="account">
          <LanguageSettings />
          <EmptyState title="Account settings arrive with passkey management" body="Change your display name, add passkeys and rotate your TOTP secret here." />
        </TabPanel>
        <TabPanel id="workspace">
          <WorkspaceDefaults />
          <EmptyState title="Workspace settings" body="Name, slug and member roles for the current workspace." />
        </TabPanel>
        <TabPanel id="notifications">
          <PushSettings />
        </TabPanel>
      </Tabs>
    </Page>
  );
}

/** Density, motion and the rail — the parts of the look that are not colour. */
function AppearanceSettings() {
  const { prefs, set, reset, syncError } = usePrefs();
  const densityId = useId();
  const motionId = useId();
  return (
    <div className="cf-settings-group">
      {syncError && (
        <p className="cf-settings-group__sync" role="status">
          {syncError} Your preferences are still saved in this browser.
        </p>
      )}
      <div className="cf-field">
        <label className="cf-field__label" htmlFor={densityId}>
          Density
        </label>
        <select
          id={densityId}
          className="cf-input"
          value={prefs.density}
          onChange={(e) => set("density", e.target.value as typeof prefs.density)}
        >
          <option value="comfortable">Comfortable</option>
          <option value="compact">Compact</option>
        </select>
        <p className="cf-field__description">Compact tightens padding across the shell. Text size stays on the theme's scale.</p>
      </div>

      <div className="cf-field">
        <label className="cf-field__label" htmlFor={motionId}>
          Motion
        </label>
        <select
          id={motionId}
          className="cf-input"
          value={prefs.reducedMotion}
          onChange={(e) => set("reducedMotion", e.target.value as typeof prefs.reducedMotion)}
        >
          <option value="system">Follow the system setting</option>
          <option value="always">Always reduce motion</option>
          <option value="never">Always animate</option>
        </select>
        <p className="cf-field__description">
          "Follow the system setting" honours <code>prefers-reduced-motion</code>; the other two override it in either
          direction.
        </p>
      </div>

      <Switch
        isSelected={prefs.navCollapsed}
        onChange={(on) => set("navCollapsed", on)}
        description="Hide the labels under the area icons. Toggle it any time with the [ shortcut."
      >
        Collapse the area rail to icons
      </Switch>

      <button type="button" className="cf-linkbtn" onClick={reset}>
        Reset appearance to defaults
      </button>
    </div>
  );
}

/** Where a session starts: which area opens, and in which workspace. */
function WorkspaceDefaults() {
  const { prefs, set } = usePrefs();
  const { data: session } = useSession();
  const areaId = useId();
  const wsId = useId();
  const areas = session ? visibleAreas(session.account.is_owner) : NAV_AREAS.filter((a) => !a.ownerOnly);
  const workspaces = session?.workspaces ?? [];
  return (
    <div className="cf-settings-group">
      <div className="cf-field">
        <label className="cf-field__label" htmlFor={areaId}>
          Open this area after signing in
        </label>
        <select id={areaId} className="cf-input" value={prefs.landingArea} onChange={(e) => set("landingArea", e.target.value)}>
          {areas.map((a) => (
            <option key={a.to} value={a.to}>
              {a.label}
            </option>
          ))}
        </select>
        <p className="cf-field__description">Applied at sign-in. It reads from this browser, so a new device starts on Home until it syncs.</p>
      </div>

      <div className="cf-field">
        <label className="cf-field__label" htmlFor={wsId}>
          Default workspace
        </label>
        <select
          id={wsId}
          className="cf-input"
          value={prefs.defaultWorkspaceId ?? ""}
          onChange={(e) => set("defaultWorkspaceId", e.target.value === "" ? null : e.target.value)}
        >
          <option value="">Whichever the server last had</option>
          {workspaces.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
        <p className="cf-field__description">Selected on sign-in only, so switching workspaces during a session still sticks.</p>
      </div>
    </div>
  );
}

function LanguageSettings() {
  const t = useT();
  const { locale, setLocale } = useLocale();
  const id = useId();
  return (
    <div className="cf-field cf-settings__language">
      <label className="cf-field__label" htmlFor={id}>
        {t("settings.language.label")}
      </label>
      <select id={id} className="cf-input" value={locale} onChange={(e) => setLocale(e.target.value as LocaleId)}>
        {Object.entries(LOCALE_NAMES).map(([value, name]) => (
          <option key={value} value={value}>
            {name}
          </option>
        ))}
      </select>
      <p className="cf-field__description">{t("settings.language.description")}</p>
    </div>
  );
}
