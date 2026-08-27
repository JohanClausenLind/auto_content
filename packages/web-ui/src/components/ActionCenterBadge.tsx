import { Button, type ButtonProps } from "react-aria-components";

export interface ActionCenterBadgeProps extends Omit<ButtonProps, "children" | "aria-label"> {
  /** Items needing the operator's attention. */
  count: number;
  /** Highest severity among pending items. */
  tone?: "neutral" | "warn" | "danger";
}

/** Placeholder trigger for the Action Center; the drawer arrives in a later phase. */
export function ActionCenterBadge({ count, tone = "neutral", className, ...props }: ActionCenterBadgeProps) {
  const label = count === 0 ? "Action Center, nothing needs attention" : `Action Center, ${count} ${count === 1 ? "item needs" : "items need"} attention`;
  return (
    <Button {...props} aria-label={label} className={["cf-action-badge", `cf-action-badge--${tone}`, typeof className === "string" ? className : ""].join(" ").trim()}>
      <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
        <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
      </svg>
      {count > 0 && (
        <span className="cf-action-badge__count" aria-hidden="true">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </Button>
  );
}
