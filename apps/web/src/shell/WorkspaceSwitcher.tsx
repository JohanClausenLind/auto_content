import { Button, Menu, MenuHeader, MenuItem, MenuSection, MenuTrigger } from "@content-factory/web-ui";
import type { Workspace } from "../api/types";

export interface WorkspaceSwitcherProps {
  workspaces: Workspace[];
  currentId: string | null;
  onSwitch: (id: string) => void;
  busy?: boolean;
}

export function WorkspaceSwitcher({ workspaces, currentId, onSwitch, busy = false }: WorkspaceSwitcherProps) {
  const current = workspaces.find((w) => w.id === currentId) ?? workspaces[0];
  const label = current ? current.name : "No workspace";
  return (
    <MenuTrigger>
      <Button variant="ghost" className="cf-ws-switcher" aria-label={`Workspace: ${label}. Switch workspace`} isDisabled={busy || workspaces.length === 0}>
        <span className="cf-ws-switcher__name">{label}</span>
        <span aria-hidden="true" className="cf-ws-switcher__chevron">▾</span>
      </Button>
      <Menu aria-label="Workspaces" placement="bottom start" onAction={(k) => onSwitch(String(k))} selectionMode="single" selectedKeys={current ? [current.id] : []}>
        <MenuSection>
          <MenuHeader className="cf-menu__header">Switch workspace</MenuHeader>
          {workspaces.map((w) => (
            <MenuItem key={w.id} id={w.id} textValue={w.name} description={w.role}>
              {w.name}
            </MenuItem>
          ))}
        </MenuSection>
      </Menu>
    </MenuTrigger>
  );
}
