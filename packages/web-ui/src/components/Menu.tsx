import {
  Menu as AriaMenu,
  MenuItem as AriaMenuItem,
  MenuTrigger,
  Popover,
  Separator,
  Header,
  MenuSection,
  type MenuProps as AriaMenuProps,
  type MenuItemProps as AriaMenuItemProps,
} from "react-aria-components";
import type { ReactNode } from "react";

export { MenuTrigger, MenuSection, Header as MenuHeader };

export interface MenuProps<T extends object> extends AriaMenuProps<T> {
  placement?: "bottom" | "bottom start" | "bottom end" | "top" | "top start" | "top end";
}

export function Menu<T extends object>({ placement = "bottom end", className, ...props }: MenuProps<T>) {
  return (
    <Popover className="cf-popover" placement={placement}>
      <AriaMenu {...props} className={["cf-menu", typeof className === "string" ? className : ""].join(" ").trim()} />
    </Popover>
  );
}

export interface MenuItemProps extends AriaMenuItemProps {
  description?: string;
  shortcut?: string;
  destructive?: boolean;
  children?: ReactNode;
}

export function MenuItem({ description, shortcut, destructive, children, ...props }: MenuItemProps) {
  const text = typeof children === "string" ? children : undefined;
  return (
    <AriaMenuItem {...props} {...(text && !props.textValue ? { textValue: text } : {})} className={["cf-menu__item", destructive ? "cf-menu__item--destructive" : ""].join(" ").trim()}>
      <span className="cf-menu__label">{children}</span>
      {description && <span className="cf-menu__description">{description}</span>}
      {shortcut && <kbd className="cf-kbd">{shortcut}</kbd>}
    </AriaMenuItem>
  );
}

export function MenuSeparator() {
  return <Separator className="cf-menu__separator" />;
}
