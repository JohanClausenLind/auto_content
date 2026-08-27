import { Tab, TabList, TabPanel, Tabs, ThemeCustomizer } from "@content-factory/web-ui";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { EmptyState, Page } from "./EmptyState";

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
          <EmptyState title="Account settings arrive with passkey management" body="Change your display name, add passkeys and rotate your TOTP secret here." />
        </TabPanel>
        <TabPanel id="workspace">
          <EmptyState title="Workspace settings" body="Name, slug and member roles for the current workspace." />
        </TabPanel>
        <TabPanel id="notifications">
          <EmptyState title="Notifications" body="Choose which events reach you by push or in the Action Center." />
        </TabPanel>
      </Tabs>
    </Page>
  );
}
