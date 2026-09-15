/** How a run reads in the workspace, shared by the run list and the pane it opens. */

import type { HistoryRun } from "../api/types";

export const OUTCOME_LABEL: Record<string, string> = {
  running: "running now",
  complete: "complete",
  review: "awaiting review",
  blocked: "blocked",
  stopped: "stopped",
  failed: "failed",
};

/** A gate and a defect want different responses, so they must not look the same. */
export const OUTCOME_TONE: Record<string, string> = {
  // A run in progress is not a result at all; it must not share the `failed` red, or a working
  // lane reads as broken while the operator watches it.
  running: "busy",
  complete: "ok",
  review: "wait",
  blocked: "wait",
  stopped: "wait",
  failed: "bad",
};

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatCost(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

/** "14:32" today, "Wed 14:32" this week, else a date. A history row is scanned, not read. */
export function formatWhen(epochSeconds: number): string {
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return "";
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const days = (Date.now() - date.getTime()) / 86_400_000;
  if (new Date().toDateString() === date.toDateString()) return time;
  if (days < 7) return `${date.toLocaleDateString([], { weekday: "short" })} ${time}`;
  return date.toLocaleDateString([], { month: "short", day: "numeric" });
}

/** The full date and time for a header; `formatWhen` is built for scanning a list. */
export function formatExact(epochSeconds: number): string {
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The day a run belongs to, as a list heading: "Today", "Yesterday", else the date. */
export function formatDay(epochSeconds: number): string {
  const date = new Date(epochSeconds * 1000);
  if (Number.isNaN(date.getTime())) return "Unknown date";
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

/** The run's own name, from its directory: `overnight~ps1c-pinecone` is the pinecone story. */
export function runLabel(run: Pick<HistoryRun, "run_id">): string {
  const parts = run.run_id.split("~");
  return parts[parts.length - 1] || run.run_id;
}
