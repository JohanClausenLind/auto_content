/**
 * Run history, docked on the left of the workspace: what this machine has made, newest first.
 *
 * The app could not see any of this. Its run list reads the `production_runs` table, which holds
 * durable Temporal runs; every film, drawing and narration actually produced here came from a
 * local run (`content-factory run-local`) that writes no row — 226 runs and 35 films, reachable
 * only by knowing the path on disk.
 *
 * It lives in the dock rather than in a window over the canvas because of what it is used for:
 * you pick a run, look at it, decide something, and then look at the next one. A modal made that
 * a sequence of open-and-close; a list on the left makes switching runs one click, with the run
 * itself open beside it (`RunPane`).
 *
 * "Needs review" is its own filter and its own badge, because it is the question the history is
 * opened with. It counts *drawings nobody has decided about* rather than runs whose last stage
 * failed: a run whose frames were all rejected is parked at the same gate, and what that one
 * needs is a redraw, not a reviewer.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { historyQuery } from "../api/queries";
import type { HistoryRun } from "../api/types";
import { formatCost, formatWhen, OUTCOME_LABEL, OUTCOME_TONE, runLabel } from "./runFormat";

/** One array, not a fresh `[]` per render: the lane list and the filtered rows are memoized on it,
 *  and a new empty literal every render throws that memoization away while the list is loading. */
const NO_RUNS: readonly HistoryRun[] = [];

export function HistoryPanel({
  selected,
  onSelect,
}: {
  selected: string | null;
  onSelect(runId: string): void;
}) {
  const runs = useQuery(historyQuery);
  const [lane, setLane] = useState<string | null>(null);
  const [waitingOnly, setWaitingOnly] = useState(false);

  // Derived during render and left to the React Compiler to memoize: a hand-written `useMemo`
  // keyed on `rows` cannot be preserved through the `??`, and the list is a few hundred rows.
  const rows = runs.data ?? NO_RUNS;
  const lanes = [...new Set(rows.map((r) => r.workflow).filter((w): w is string => !!w))].sort();
  const waiting = rows.filter((r) => r.awaiting_review > 0);
  const shown = rows.filter(
    (r) => (lane === null || r.workflow === lane) && (!waitingOnly || r.awaiting_review > 0),
  );

  return (
    <section className="cf-hist" aria-label="Run history">
      <header className="cf-hist__head">
        <h2 className="cf-hist__title">History</h2>
        <span className="cf-hist__count">{rows.length} runs</span>
      </header>

      <nav aria-label="Lanes" className="cf-hist__nav">
        <ul className="cf-hist__lanes">
          <li>
            <button type="button" aria-pressed={lane === null && !waitingOnly} onClick={() => { setLane(null); setWaitingOnly(false); }}>
              All runs<span className="cf-hist__count">{rows.length}</span>
            </button>
          </li>
          <li>
            <button
              type="button"
              className="cf-hist__lane--review"
              aria-pressed={waitingOnly}
              onClick={() => { setWaitingOnly((v) => !v); setLane(null); }}
            >
              Needs review
              <span className="cf-hist__count">
                {waiting.reduce((sum, r) => sum + r.awaiting_review, 0)}
              </span>
            </button>
          </li>
          {lanes.map((l) => (
            <li key={l}>
              <button type="button" aria-pressed={lane === l} onClick={() => setLane(lane === l ? null : l)}>
                {l}
                <span className="cf-hist__count">{rows.filter((r) => r.workflow === l).length}</span>
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <div className="cf-hist__list">
        <ul aria-label="Runs">
          {shown.map((run) => (
            <li key={run.run_id}>
              <button
                type="button"
                className="cf-hist__row"
                aria-pressed={run.run_id === selected}
                onClick={() => onSelect(run.run_id)}
              >
                <span className="cf-hist__rowname">{runLabel(run)}</span>
                <span className="cf-hist__rowlane">{run.workflow ?? "—"}</span>
                <span className="cf-hist__rowstate" data-tone={OUTCOME_TONE[run.outcome] ?? "wait"}>
                  {OUTCOME_LABEL[run.outcome] ?? run.outcome}
                </span>
                {run.awaiting_review > 0 && (
                  <span className="cf-hist__rowbadge">{run.awaiting_review} to review</span>
                )}
                {/* Cost and time share one flex cell rather than being pushed apart by a fixed
                    margin: at 21rem, "7m" and "Wed 03:12" ran into each other as "7mWed 03:12". */}
                <span className="cf-hist__rowmeta">
                  <span className="cf-hist__rowcost">{formatCost(run.seconds)}</span>
                  <span className="cf-hist__rowwhen">{formatWhen(run.finished_at)}</span>
                </span>
              </button>
            </li>
          ))}
          {runs.isPending && <li className="cf-hist__empty">Reading run reports…</li>}
          {!runs.isPending && shown.length === 0 && (
            <li className="cf-hist__empty">
              {waitingOnly ? "Nothing is waiting for a review." : "No runs yet."}
            </li>
          )}
        </ul>
      </div>
    </section>
  );
}
