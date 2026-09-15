/** Run history docked on the left: local runs newest first, grouped by day. */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { historyQuery } from "../api/queries";
import type { HistoryRun } from "../api/types";
import { formatCost, formatDay, formatWhen, OUTCOME_LABEL, OUTCOME_TONE, runLabel } from "./runFormat";

/** One array, not a fresh `[]` per render: the lane list. */
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
  const [search, setSearch] = useState("");

  // Derived during render and left to the React Compiler to memoize: a hand-written `useMemo`
  // keyed on `rows` cannot be preserved through the `??`, and the list is a few hundred rows.
  const rows = runs.data ?? NO_RUNS;
  const lanes = [...new Set(rows.map((r) => r.workflow).filter((w): w is string => !!w))].sort();
  const waiting = rows.filter((r) => r.awaiting_review > 0);
  const needle = search.trim().toLowerCase();
  const shown = rows.filter(
    (r) =>
      (lane === null || r.workflow === lane) &&
      (!waitingOnly || r.awaiting_review > 0) &&
      (needle === "" ||
        r.run_id.toLowerCase().includes(needle) ||
        (r.subject ?? "").toLowerCase().includes(needle) ||
        (r.workflow ?? "").toLowerCase().includes(needle)),
  );

  // Day headings, in the order the rows already are (newest first), so "what did last night make"
  // is answered by the list's own shape rather than by reading 241 timestamps.
  const days: { day: string; runs: HistoryRun[] }[] = [];
  for (const run of shown) {
    const day = formatDay(run.finished_at);
    const last = days[days.length - 1];
    if (last && last.day === day) last.runs.push(run);
    else days.push({ day, runs: [run] });
  }

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

      <div className="cf-hist__search">
        <input
          type="search"
          className="cf-hist__searchinput"
          aria-label="Search runs"
          placeholder="Search what was rendered…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>

      <div className="cf-hist__list">
        <ul aria-label="Runs">
          {days.map(({ day, runs: dayRuns }) => (
            <li key={day}>
              <p className="cf-hist__day">{day}</p>
              <ul>
                {dayRuns.map((run) => (
                  <li key={run.run_id}>
                    <button
                      type="button"
                      className="cf-hist__row"
                      aria-pressed={run.run_id === selected}
                      onClick={() => onSelect(run.run_id)}
                    >
                      <span className="cf-hist__rowname">{runLabel(run)}</span>
                      {/* What it was rendering. The line that turns a directory name into a run
                          somebody can recognise, so it gets the width and the row's own voice. */}
                      {run.subject && <span className="cf-hist__rowsubject">{run.subject}</span>}
                      <span className="cf-hist__rowlane">{run.workflow ?? "—"}</span>
                      <span
                        className="cf-hist__rowstate"
                        data-tone={OUTCOME_TONE[run.outcome] ?? "wait"}
                      >
                        {OUTCOME_LABEL[run.outcome] ?? run.outcome}
                      </span>
                      {run.awaiting_review > 0 && (
                        <span className="cf-hist__rowbadge">{run.awaiting_review} to review</span>
                      )}
                      {/* Cost and time share one flex cell rather than being pushed apart by a
                         fixed margin: at 21rem, "7m". */}
                      <span className="cf-hist__rowmeta">
                        <span className="cf-hist__rowcost">{formatCost(run.seconds)}</span>
                        <span className="cf-hist__rowwhen">{formatWhen(run.finished_at)}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </li>
          ))}
          {runs.isPending && <li className="cf-hist__empty">Reading run reports…</li>}
          {!runs.isPending && shown.length === 0 && (
            <li className="cf-hist__empty">
              {needle
                ? `Nothing matches "${search.trim()}".`
                : waitingOnly
                  ? "Nothing is waiting for a review."
                  : "No runs yet."}
            </li>
          )}
        </ul>
      </div>
    </section>
  );
}
