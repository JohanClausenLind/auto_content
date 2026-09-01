import { Button } from "@content-factory/web-ui";
import { useQuery } from "@tanstack/react-query";
import { isApiError } from "../api/client";
import { opsAuditQuery, opsHealthQuery } from "../api/queries";
import type { HealthStatus } from "../api/types";
import { EmptyState, ErrorState, LoadingState, Page } from "./EmptyState";
import { formatWhen } from "./runState";

const TEMPORAL_UI = "http://127.0.0.1:8233";

const STATUS_LABEL: Record<string, string> = {
  ok: "OK",
  warn: "Warning",
  fail: "Failing",
  skip: "Skipped",
};

function statusLabel(status: HealthStatus): string {
  return STATUS_LABEL[status] ?? status;
}

export function OperationsPage() {
  const health = useQuery(opsHealthQuery);
  const audit = useQuery(opsAuditQuery);

  return (
    <Page title="Operations" lead="Owner tools: how healthy the factory is, and who did what.">
      <section className="cf-ops__section" aria-labelledby="cf-ops-health">
        <div className="cf-ops__sectionhead">
          <h2 id="cf-ops-health" className="cf-ops__heading">
            Health
          </h2>
          <Button variant="secondary" size="sm" onPress={() => void health.refetch()} isDisabled={health.isFetching} isPending={health.isFetching}>
            {health.isFetching ? "Checking…" : "Refresh"}
          </Button>
        </div>
        {health.isPending ? (
          <LoadingState label="Running checks…" />
        ) : health.isError ? (
          <ErrorState title="Couldn't run the health checks" {...(isApiError(health.error) ? { detail: health.error.detail } : {})} retry={() => void health.refetch()} />
        ) : (
          <>
            <p className="cf-ops__verdict" role="status">
              {health.data.ok ? "Everything the factory needs is in place." : "Something needs attention — look for the failing rows below."}
            </p>
            <div className="cf-runs__tablewrap">
              <table className="cf-runs cf-ops__table">
                <caption className="cf-visually-hidden">Health checks</caption>
                <thead>
                  <tr>
                    <th scope="col">Check</th>
                    <th scope="col">Status</th>
                    <th scope="col">Detail</th>
                    <th scope="col">How to fix</th>
                  </tr>
                </thead>
                <tbody>
                  {health.data.checks.map((check) => (
                    <tr key={check.name}>
                      <th scope="row">{check.name}</th>
                      <td>
                        <span className="cf-healthchip" data-status={check.status}>
                          {statusLabel(check.status)}
                        </span>
                      </td>
                      <td className="cf-ops__detail">{check.detail}</td>
                      <td className="cf-ops__fix">{check.fix ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section className="cf-ops__section" aria-labelledby="cf-ops-audit">
        <h2 id="cf-ops-audit" className="cf-ops__heading">
          Recent activity
        </h2>
        <p className="cf-ops__lead">The last 50 recorded actions, newest first. Every approval, creation and sign-in lands here.</p>
        {audit.isPending ? (
          <LoadingState label="Loading the audit trail…" />
        ) : audit.isError ? (
          <ErrorState title="Couldn't load the audit trail" {...(isApiError(audit.error) ? { detail: audit.error.detail } : {})} retry={() => void audit.refetch()} />
        ) : audit.data.length === 0 ? (
          <EmptyState title="Nothing recorded yet" body="Actions show up here as soon as someone creates, approves or changes something." />
        ) : (
          <div className="cf-runs__tablewrap">
            <table className="cf-runs cf-ops__table">
              <caption className="cf-visually-hidden">Audit trail</caption>
              <thead>
                <tr>
                  <th scope="col">Action</th>
                  <th scope="col">Who</th>
                  <th scope="col">Target</th>
                  <th scope="col">When</th>
                </tr>
              </thead>
              <tbody>
                {audit.data.map((event) => (
                  <tr key={event.id}>
                    <td>{event.action}</td>
                    <td>{event.actor ?? "—"}</td>
                    <td className="cf-ops__target">{event.target ?? "—"}</td>
                    <td>{formatWhen(event.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="cf-ops__temporal">
        For step-by-step workflow internals, open{" "}
        <a href={TEMPORAL_UI} target="_blank" rel="noreferrer">
          Workflow engine (low-level)
        </a>
        .
      </p>
    </Page>
  );
}
