import { Button, Menu, MenuItem, MenuSeparator, MenuTrigger } from "@content-factory/web-ui";
import type { Account, Meta } from "../api/types";

export interface UserMenuProps {
  account: Account;
  meta: Meta | undefined;
  onSettings: () => void;
  onCustomizeTheme: () => void;
  onSignOut: () => void;
}

export function UserMenu({ account, meta, onSettings, onCustomizeTheme, onSignOut }: UserMenuProps) {
  const initials = account.display_name
    .split(/\s+/)
    .map((p) => p[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <MenuTrigger>
      <Button variant="ghost" className="cf-user-menu" aria-label={`Account menu for ${account.display_name}`}>
        <span className="cf-avatar" aria-hidden="true">
          {initials || "?"}
        </span>
      </Button>
      <Menu
        aria-label="Account"
        onAction={(k) => {
          if (k === "settings") onSettings();
          else if (k === "theme") onCustomizeTheme();
          else if (k === "signout") onSignOut();
        }}
      >
        <MenuItem id="who" isDisabled textValue={account.display_name} description={account.is_owner ? "Owner" : `@${account.username}`}>
          {account.display_name}
        </MenuItem>
        <MenuSeparator />
        <MenuItem id="settings">Settings</MenuItem>
        <MenuItem id="theme">Customize theme…</MenuItem>
        <MenuSeparator />
        <MenuItem id="signout" destructive>
          Sign out
        </MenuItem>
        <MenuSeparator />
        <MenuItem id="version" isDisabled textValue="Version" description={meta ? `${meta.environment}` : "…"}>
          {meta ? `Content Factory ${meta.version}` : "Loading version…"}
        </MenuItem>
      </Menu>
    </MenuTrigger>
  );
}
