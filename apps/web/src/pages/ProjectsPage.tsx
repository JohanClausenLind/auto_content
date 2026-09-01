import { Button } from "@content-factory/web-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { api, isApiError } from "../api/client";
import { queryKeys, runsQuery } from "../api/queries";
import type { RunQuality } from "../api/types";
import { EmptyState, ErrorState, LoadingState, Page } from "./EmptyState";
import { formatWhen, runStateLabel } from "./runState";

export function ProjectsPage() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const runs = useQuery(runsQuery);
  const [quality, setQuality] = useState<RunQuality>("demo");

  const startRun = useMutation({
    mutationFn: () => api.runs.start(quality),
    onSuccess: ({ run_id }) => {
      void client.invalidateQueries({ queryKey: queryKeys.runs });
      void navigate({ to: "/projects/$runId", params: { runId: run_id } });
    },
  });

  return (
    <Page title="Projects" lead="Every pipeline run, newest first. Open one to watch it move.">
      <div className="cf-runs__toolbar">
        <label className="cf-runs__quality">
          <span>Quality</span>
          <select className="cf-input cf-runs__select" value={quality} onChange={(e) => setQuality(e.target.value as RunQuality)}>
            <option value="smoke">smoke</option>
            <option value="demo">demo</option>
          </select>
        </label>
        <Button variant="primary" onPress={() => startRun.mutate()} isDisabled={startRun.isPending} isPending={startRun.isPending}>
          {startRun.isPending ? "Starting…" : `Start ${quality} run`}
        </Button>
      </div>
      {startRun.error && (
        <p role="alert" className="cf-runs__error">
          {isApiError(startRun.error) ? startRun.error.detail : "Couldn't start the run."}
        </p>
      )}

      {runs.isPending ? (
        <LoadingState label="Loading runs…" />
      ) : runs.isError ? (
        <ErrorState title="Couldn't load runs" {...(isApiError(runs.error) ? { detail: runs.error.detail } : {})} retry={() => void runs.refetch()} />
      ) : runs.data.length === 0 ? (
        <EmptyState title="No runs yet" body="Start a demo run to watch the pipeline produce a campaign end to end, with fixtures and no external calls." />
      ) : (
        <div className="cf-runs__tablewrap">
          <table className="cf-runs">
            <caption className="cf-visually-hidden">Pipeline runs</caption>
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">State</th>
                <th scope="col">Quality</th>
                <th scope="col">Campaign</th>
                <th scope="col">Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map((run) => (
                <tr key={run.run_id}>
                  <td>
                    <Link className="cf-runs__link" to="/projects/$runId" params={{ runId: run.run_id }}>
                      {run.run_id}
                    </Link>
                  </td>
                  <td>
                    <span className="cf-runstate" data-state={run.state}>
                      {runStateLabel(run.state)}
                    </span>
                  </td>
                  <td>{run.quality}</td>
                  <td>{run.campaign_id}</td>
                  <td>{formatWhen(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
