/**
 * The frame-review gate, answered where the pictures are.
 *
 * `review_frames` is where this machine's work actually stops: it draws every frame, measures
 * what can be measured, writes a contact sheet, and then blocks on a person looking — because the
 * faults that got through were two characters standing back to back, four figures where two were
 * staged, "honey-coloured" drawn as jars of honey. None of that is measurable; all of it is
 * obvious in the picture.
 *
 * Until now the only way to answer was `content-factory frames review` with the run's path on
 * disk, so 25 runs are parked at the gate with their drawings finished. This panel is the same
 * decision with the images in front of you: accept the batch, or reject the ones that are wrong
 * and say why — the reason is what the redraw and the prompt-guidance proposals read.
 *
 * Two things it does not pretend. A verdict unblocks the gate and makes nothing, so the command
 * that continues the run is shown next to the result; and a frame whose file no longer matches
 * what the gate measured is labelled, because approving it would approve a picture nobody saw.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, isApiError } from "../api/client";
import { queryKeys } from "../api/queries";
import type { FrameVerdict, ReviewFrame, ReviewFinding, RunReview, VerdictRefusal } from "../api/types";

type Mark = "accept" | "reject";

function refusalOf(error: unknown): VerdictRefusal | null {
  if (!isApiError(error)) return null;
  const detail = (error.data as { detail?: unknown } | null)?.detail;
  if (!detail || typeof detail !== "object") return null;
  const refusal = detail as Partial<VerdictRefusal>;
  return typeof refusal.problem === "string"
    ? { problem: refusal.problem, kind: String(refusal.kind ?? ""), frames: refusal.frames ?? [], details: refusal.details ?? [] }
    : null;
}

function Findings({ findings }: { findings: readonly ReviewFinding[] }) {
  const failed = findings.filter((f) => !f.passed);
  if (findings.length === 0) return null;
  return (
    <div className="cf-review__findings">
      {failed.map((f) => (
        <p key={f.check} className="cf-review__finding" data-severity={f.severity}>
          {f.detail}
        </p>
      ))}
      {/* Everything measured, folded away: the failed ones are what to look at first, and the
          rest are what you want when a picture looks wrong and nothing was flagged. */}
      <details className="cf-review__all">
        <summary>{findings.length} measurements</summary>
        <ul>
          {findings.map((f) => (
            <li key={f.check} data-passed={f.passed || undefined}>
              <span>{f.check}</span> {f.detail}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function FrameCard({
  runId,
  frame,
  mark,
  onMark,
}: {
  runId: string;
  frame: ReviewFrame;
  mark: Mark | undefined;
  onMark(mark: Mark | undefined): void;
}) {
  const src = frame.image ? api.history.fileUrl(runId, frame.image) : null;
  return (
    <li className="cf-review__frame" data-mark={mark} data-blocked={frame.blocked || undefined}>
      <figure className="cf-review__shot">
        {src ? (
          // A new tab is the full-size view: the browser already has an image viewer, and the
          // whole point of the gate is looking at the picture properly.
          <a href={src} target="_blank" rel="noreferrer">
            <img src={src} alt={frame.frame_id} loading="lazy" />
          </a>
        ) : (
          <p className="cf-review__missing">This frame&apos;s file is not where the run left it.</p>
        )}
        <figcaption>{frame.frame_id}</figcaption>
      </figure>
      {!frame.on_disk && frame.image && (
        <p className="cf-review__stale">
          This file changed after the gate measured it — the gate will ask again about the new one.
        </p>
      )}
      {frame.blocked && (
        <p className="cf-review__stale">A measurement failed on its own; this frame is rejected whatever is decided.</p>
      )}
      <Findings findings={frame.findings} />
      {frame.reason && <p className="cf-review__reason">Rejected: {frame.reason}</p>}
      <div className="cf-review__marks" role="group" aria-label={`Verdict for ${frame.frame_id}`}>
        <button
          type="button"
          className="cf-review__mark"
          data-mark="accept"
          aria-pressed={mark === "accept"}
          onClick={() => onMark(mark === "accept" ? undefined : "accept")}
        >
          Accept
        </button>
        <button
          type="button"
          className="cf-review__mark"
          data-mark="reject"
          aria-pressed={mark === "reject"}
          onClick={() => onMark(mark === "reject" ? undefined : "reject")}
        >
          Reject
        </button>
      </div>
    </li>
  );
}

const SEEDED: Record<FrameVerdict, Mark | undefined> = {
  accept: "accept",
  reject: "reject",
  unreviewed: undefined,
};

export function FrameReview({
  runId,
  review,
  resumeCommand,
}: {
  runId: string;
  review: RunReview;
  resumeCommand: string;
}) {
  const client = useQueryClient();
  // Seeded from what is on disk, so a second visit shows the decisions already recorded rather
  // than an empty form that would quietly un-accept them when submitted.
  const seeded = useMemo(() => {
    const marks: Record<string, Mark> = {};
    for (const frame of review.frames) {
      const mark = SEEDED[frame.verdict];
      if (mark) marks[frame.frame_id] = mark;
    }
    return marks;
  }, [review.frames]);
  const [marks, setMarks] = useState<Record<string, Mark>>(seeded);
  const [reason, setReason] = useState(review.frames.find((f) => f.reason)?.reason ?? "");
  const [note, setNote] = useState("");

  const record = useMutation({
    mutationFn: (body: Parameters<typeof api.history.recordVerdict>[1]) =>
      api.history.recordVerdict(runId, { deliverable: review.deliverable, ...body }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.runReview(runId) });
      void client.invalidateQueries({ queryKey: queryKeys.historyRun(runId) });
      void client.invalidateQueries({ queryKey: queryKeys.history });
    },
  });

  const accepted = Object.entries(marks).filter(([, m]) => m === "accept").map(([id]) => id);
  const rejected = Object.entries(marks).filter(([, m]) => m === "reject").map(([id]) => id);
  const needsReason = rejected.length > 0 && !reason.trim();
  const refusal = refusalOf(record.error);

  const setMark = (frameId: string, mark: Mark | undefined) =>
    setMarks((current) => {
      const next = { ...current };
      if (mark) next[frameId] = mark;
      else delete next[frameId];
      return next;
    });

  return (
    <section className="cf-review" aria-label="Frame review">
      <header className="cf-review__head">
        <h3 className="cf-review__title">
          {review.unreviewed > 0
            ? `${review.unreviewed} drawing${review.unreviewed === 1 ? "" : "s"} waiting for you`
            : review.passed
              ? "Reviewed and passed"
              : `${review.rejected} drawing${review.rejected === 1 ? "" : "s"} rejected`}
        </h3>
        <p className="cf-review__lead">
          Nothing is cut from a frame nobody has looked at. Accept the batch, or reject the ones that
          are wrong and say why — the reason is what the redraw is given.
        </p>
        <p className="cf-review__meta">
          {review.frames.length} frames · {review.flagged} with a flagged measurement
          {review.reviewer ? ` · last decided by ${review.reviewer}` : ""}
          {review.contact_sheet && (
            <>
              {" · "}
              <a href={api.history.fileUrl(runId, review.contact_sheet)} target="_blank" rel="noreferrer">
                contact sheet
              </a>
            </>
          )}
        </p>
      </header>

      <ul className="cf-review__frames">
        {review.frames.map((frame) => (
          <FrameCard
            key={frame.frame_id}
            runId={runId}
            frame={frame}
            mark={marks[frame.frame_id]}
            onMark={(mark) => setMark(frame.frame_id, mark)}
          />
        ))}
      </ul>

      <div className="cf-review__decide">
        <label className="cf-review__field">
          <span>Why the rejected ones are wrong</span>
          <textarea
            value={reason}
            rows={2}
            maxLength={400}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. two figures where one was staged"
          />
        </label>
        <label className="cf-review__field">
          <span>Note kept with the verdict (optional)</span>
          <textarea value={note} rows={2} maxLength={2000} onChange={(e) => setNote(e.target.value)} />
        </label>
        <div className="cf-review__actions">
          <button
            type="button"
            className="cf-review__submit"
            disabled={record.isPending || needsReason || (accepted.length === 0 && rejected.length === 0)}
            onClick={() => record.mutate({ accept: accepted, reject: rejected, reason: reason.trim(), note: note.trim() })}
          >
            {record.isPending ? "Recording…" : `Record verdict (${accepted.length} accepted, ${rejected.length} rejected)`}
          </button>
          <button
            type="button"
            className="cf-review__acceptall"
            disabled={record.isPending || needsReason}
            onClick={() =>
              record.mutate({ accept_rest: true, reject: rejected, reason: reason.trim(), note: note.trim() })
            }
          >
            {rejected.length > 0 ? "Accept the rest" : `Accept all ${review.frames.length}`}
          </button>
        </div>
        {needsReason && (
          <p className="cf-review__hint">
            A rejection needs a reason: it is what the redraw is told, and what the prompt-guidance
            proposals are built from.
          </p>
        )}
        {refusal && (
          <div className="cf-review__refused" role="alert">
            <p>{refusal.problem}</p>
            {refusal.frames.length > 0 && <p className="cf-review__hint">{refusal.frames.join(", ")}</p>}
            {refusal.details.map((detail) => (
              <p key={detail} className="cf-review__hint">
                {detail}
              </p>
            ))}
          </div>
        )}
        {record.isError && !refusal && (
          <p className="cf-review__refused" role="alert">
            {isApiError(record.error) ? record.error.detail : "Could not record the verdict."}
          </p>
        )}
        {record.isSuccess && !record.isPending && (
          <div className="cf-review__done" role="status">
            <p>
              Verdict recorded{review.passed ? " — the gate is satisfied" : ""}. A verdict unblocks the
              gate; it does not make anything. Continue the run with:
            </p>
            <code>{resumeCommand}</code>
          </div>
        )}
      </div>
    </section>
  );
}
