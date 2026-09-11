import type { RunState } from "../api/types";

/** Short label for pills and tables. */
export const RUN_STATE_LABEL: Record<RunState, string> = {
  CREATED: "Created",
  PREFLIGHTING: "Preflighting",
  WAITING_FOR_APPROVAL: "Waiting for approval",
  APPROVED: "Approved",
  PRODUCING: "Producing",
  COMPLETE: "Complete",
  BLOCKED: "Blocked",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
};

/** Plain-language sentence for the run header. */
export const RUN_STATE_EXPLANATION: Record<RunState, string> = {
  CREATED: "This run is queued and will start shortly.",
  PREFLIGHTING: "The factory is checking the plan before making anything.",
  WAITING_FOR_APPROVAL: "Nothing is produced until you approve the plan below.",
  APPROVED: "The plan is approved; production is about to start.",
  PRODUCING: "The factory is producing content right now.",
  COMPLETE: "Everything finished and the deliverables are ready.",
  BLOCKED: "The run hit something it can't resolve on its own.",
  FAILED: "The run stopped because a step failed.",
  CANCELLED: "This run was cancelled and won't continue.",
};

export function runStateLabel(state: RunState | (string & {})): string {
  return RUN_STATE_LABEL[state as RunState] ?? state;
}

export function runStateExplanation(state: RunState | (string & {})): string {
  return RUN_STATE_EXPLANATION[state as RunState] ?? "";
}

export function formatWhen(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

/**
 * A finish time as a clock reading: "14:32", or "Thu 14:32" when it is not today. An estimate
 * minutes away is easiest to act on as a time of day, and one that lands tomorrow has to say so
 * or it reads as being twelve hours early.
 */
export function formatFinish(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const today = new Date().toDateString() === date.toDateString();
  return today ? time : `${date.toLocaleDateString([], { weekday: "short" })} ${time}`;
}
