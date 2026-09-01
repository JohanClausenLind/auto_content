import { useQuery } from "@tanstack/react-query";
import { Link, type LinkProps } from "@tanstack/react-router";
import { isApiError } from "../api/client";
import { actionItemsQuery } from "../api/queries";
import type { ActionItem } from "../api/types";
import { EmptyState, ErrorState, LoadingState } from "./EmptyState";
import { formatWhen } from "./runState";

/** The API hands back app paths like "/projects/run_1"; the route union can't know them statically. */
function deepLinkProps(to: string): Pick<LinkProps, "to"> {
  return { to } as unknown as Pick<LinkProps, "to">;
}

function severityLabel(severity: ActionItem["severity"]): string {
  switch (severity) {
    case "critical":
      return "Critical";
    case "warning":
      return "Warning";
    case "info":
      return "Info";
    default:
      return severity;
  }
}

/** Open items that need the operator, each deep-linking into the app. */
export function ActionCenter() {
  const items = useQuery(actionItemsQuery);

  if (items.isPending) return <LoadingState label="Checking what needs you…" />;
  if (items.isError) {
    return <ErrorState title="Couldn't load your action items" {...(isApiError(items.error) ? { detail: items.error.detail } : {})} retry={() => void items.refetch()} />;
  }
  if (items.data.length === 0) {
    return (
      <EmptyState
        title="Nothing needs you right now"
        body="When a run pauses for approval, gets blocked, or fails, it shows up here with a link straight to the right place."
        action={
          <Link to="/projects" className="cf-button cf-button--secondary cf-button--md">
            See your runs
          </Link>
        }
      />
    );
  }

  return (
    <section className="cf-actioncenter" aria-labelledby="cf-actioncenter-title">
      <h2 id="cf-actioncenter-title" className="cf-actioncenter__heading">
        Needs you ({items.data.length})
      </h2>
      <ul className="cf-actionlist">
        {items.data.map((item) => (
          <li key={item.id} className="cf-actionitem" data-severity={item.severity}>
            <Link {...deepLinkProps(item.deep_link)} className="cf-actionitem__link">
              <span className="cf-actionitem__severity" data-severity={item.severity}>
                {severityLabel(item.severity)}
              </span>
              <span className="cf-actionitem__text">
                <span className="cf-actionitem__title">{item.title}</span>
                <span className="cf-actionitem__body">{item.body}</span>
              </span>
              <span className="cf-actionitem__when">{formatWhen(item.created_at)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
