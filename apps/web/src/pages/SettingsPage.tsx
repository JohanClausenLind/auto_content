import { Tab, TabList, TabPanel, Tabs, ThemeCustomizer } from "@content-factory/web-ui";
import { useId } from "react";
import { useLocale, useT } from "../i18n";
import { LOCALE_NAMES, type LocaleId } from "../i18n/messages";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { EmptyState, Page } from "./EmptyState";
import { PushSettings } from "./PushSettings";

export type SettingsTab = "themes" | "account" | "workspace" | "notifications";
const TABS: SettingsTab[] = ["themes", "account", "workspace", "notifications"];

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
          <Tab id="account">Account</Tab>
          <Tab id="workspace">Workspace</Tab>
          <Tab id="notifications">Notifications</Tab>
        </TabList>
        <TabPanel id="themes">
          <ThemeCustomizer />
        </TabPanel>
        <TabPanel id="account">
          <LanguageSettings />
          <EmptyState title="Account settings arrive with passkey management" body="Change your display name, add passkeys and rotate your TOTP secret here." />
        </TabPanel>
        <TabPanel id="workspace">
          <EmptyState title="Workspace settings" body="Name, slug and member roles for the current workspace." />
        </TabPanel>
        <TabPanel id="notifications">
          <PushSettings />
        </TabPanel>
      </Tabs>
    </Page>
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
