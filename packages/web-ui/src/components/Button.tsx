import { Button as AriaButton, type ButtonProps as AriaButtonProps } from "react-aria-components";

export interface ButtonProps extends AriaButtonProps {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
}

export function Button({ variant = "secondary", size = "md", className, ...props }: ButtonProps) {
  const cls = ["cf-button", `cf-button--${variant}`, `cf-button--${size}`, typeof className === "string" ? className : ""].join(" ").trim();
  return <AriaButton {...props} className={cls} />;
}
