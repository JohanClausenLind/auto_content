import type { ReactNode } from "react";

export interface EmptyStateProps {
  title: string;
  /** One plain sentence about what will live here and how it starts. */
  body: string;
  action?: ReactNode;
  icon?: ReactNode;
}

/** Designed empty state: what this area is for, and the one next step. */
export function EmptyState({ title, body, action, icon }: EmptyStateProps) {
  return (
    <div className="cf-empty">
      <div className="cf-empty__icon" aria-hidden="true">
        {icon ?? <span className="cf-empty__glyph" />}
      </div>
      <h2 className="cf-empty__title">{title}</h2>
      <p className="cf-empty__body">{body}</p>
      {action && <div className="cf-empty__action">{action}</div>}
    </div>
  );
}

export function Page({ title, lead, children }: { title: string; lead?: string | undefined; children: ReactNode }) {
  return (
    <article className="cf-page">
      <header className="cf-page__header">
        <h1 className="cf-page__title">{title}</h1>
        {lead && <p className="cf-page__lead">{lead}</p>}
      </header>
      {children}
    </article>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="cf-loading" role="status" aria-live="polite">
      <span className="cf-loading__spinner" aria-hidden="true" />
      {label}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", detail, retry }: { title?: string; detail?: string; retry?: () => void }) {
  return (
    <div className="cf-error" role="alert">
      <h2 className="cf-error__title">{title}</h2>
      {detail && <p className="cf-error__detail">{detail}</p>}
      {retry && (
        <button type="button" className="cf-button cf-button--secondary cf-button--sm" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  );
}
