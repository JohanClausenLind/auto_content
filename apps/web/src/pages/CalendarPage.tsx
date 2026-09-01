import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { isApiError } from "../api/client";
import { runsQuery } from "../api/queries";
import type { RunSummary } from "../api/types";
import { EmptyState, ErrorState, LoadingState, Page } from "./EmptyState";
import { runStateLabel } from "./runState";

function dayOf(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
}

function timeOf(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/** Newest day first; runs within a day keep the API's order (newest first). */
export function groupRunsByDay(runs: RunSummary[]): { day: string; runs: RunSummary[] }[] {
  const sorted = [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const groups: { day: string; runs: RunSummary[] }[] = [];
  for (const run of sorted) {
    const day = dayOf(run.created_at);
    const last = groups[groups.length - 1];
    if (last && last.day === day) last.runs.push(run);
    else groups.push({ day, runs: [run] });
  }
  return groups;
}

export function CalendarPage() {
  const runs = useQuery(runsQuery);

  return (
    <Page title="Calendar" lead="What went out, and eventually what's scheduled to go out.">
      <section className="cf-calendar__notice">
        <h2 className="cf-calendar__noticetitle">Scheduling isn't built yet</h2>
        <p className="cf-calendar__noticebody">
          A real calendar — pick a date, the factory publishes then — arrives with phase 9 (destinations and scheduling). Nothing here pretends otherwise. Until then, this page lists recent
          pipeline runs by day so you can see the factory's rhythm.
        </p>
      </section>

      {runs.isPending ? (
        <LoadingState label="Loading recent runs…" />
      ) : runs.isError ? (
        <ErrorState title="Couldn't load runs" {...(isApiError(runs.error) ? { detail: runs.error.detail } : {})} retry={() => void runs.refetch()} />
      ) : runs.data.length === 0 ? (
        <EmptyState title="No runs yet" body="Once you create a campaign, its runs appear here grouped by day." />
      ) : (
        groupRunsByDay(runs.data).map((group) => (
          <section key={group.day} className="cf-calendar__day" aria-label={group.day}>
            <h2 className="cf-calendar__dayheading">{group.day}</h2>
            <ul className="cf-calendar__list">
              {group.runs.map((run) => (
                <li key={run.run_id} className="cf-calendar__run">
                  <Link className="cf-runs__link" to="/projects/$runId" params={{ runId: run.run_id }}>
                    {run.run_id}
                  </Link>
                  <span className="cf-runstate" data-state={run.state}>
                    {runStateLabel(run.state)}
                  </span>
                  <span className="cf-calendar__time">{timeOf(run.created_at)}</span>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </Page>
  );
}
