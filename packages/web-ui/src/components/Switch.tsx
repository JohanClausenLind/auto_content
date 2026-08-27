import { Switch as AriaSwitch, type SwitchProps as AriaSwitchProps } from "react-aria-components";
import type { ReactNode } from "react";

export interface SwitchProps extends Omit<AriaSwitchProps, "children"> {
  children: ReactNode;
  description?: string;
}

export function Switch({ children, description, className, ...props }: SwitchProps) {
  return (
    <AriaSwitch {...props} className={["cf-switch", typeof className === "string" ? className : ""].join(" ").trim()}>
      <span className="cf-switch__track" aria-hidden="true">
        <span className="cf-switch__thumb" />
      </span>
      <span className="cf-switch__text">
        <span className="cf-switch__label">{children}</span>
        {description && <span className="cf-switch__description">{description}</span>}
      </span>
    </AriaSwitch>
  );
}
