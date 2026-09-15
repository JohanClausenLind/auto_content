/** The vision model's second opinion beside the pictures: it never records a verdict. */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, isApiError } from "../api/client";
import { queryKeys } from "../api/queries";
import type { AiSetReview, FrameOpinion, OpinionSeverity } from "../api/types";

const SEVERITY_LABEL: Record<OpinionSeverity, string> = {
  fine: "fine",
  minor: "worth a look",
  wrong: "cannot be used",
};

function when(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function Opinion({ frame }: { frame: FrameOpinion }) {
  return (
    <li className="cf-airev__frame" data-severity={frame.severity}>
      <p className="cf-airev__id">
        <code>{frame.frame_id}</code>
        <span className="cf-airev__sev" data-severity={frame.severity}>
          {SEVERITY_LABEL[frame.severity]}
        </span>
        {!frame.matches_intent && <span className="cf-airev__sev" data-severity="wrong">not what was asked for</span>}
      </p>
      {/* The load-bearing line. Read it against the picture: that is the whole check on the
          reviewer, and it is why it is the sentence and not a badge. */}
      <p className="cf-airev__shows">{frame.shows}</p>
      {frame.issues.length > 0 && (
        <ul className="cf-airev__issues">
          {frame.issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function AiReview({
  runId,
  deliverable,
  review,
  onMarkFlagged,
}: {
  runId: string;
  deliverable: string;
  review: AiSetReview | undefined;
  /** Pre-mark the frames the model would not pass in the operator's own selection. */
  onMarkFlagged(frameIds: readonly string[]): void;
}) {
  const client = useQueryClient();
  const ask = useMutation({
    mutationFn: () => api.history.aiReview(runId, deliverable),
    // The response is the review page with the stored opinion on it, so it goes into the cache;
    // invalidating would throw away an answer that cost forty seconds of GPU.
    onSuccess: (page) => client.setQueryData(queryKeys.runReview(runId), page),
  });

  const problem = isApiError(ask.error)
    ? ask.error.status === 409
      ? ask.error.detail // cannot be asked for: a run holds the card, or no frames on disk
      : `The vision reviewer did not answer. ${ask.error.detail}`
    : ask.isError
      ? "Could not reach the vision reviewer."
      : null;

  return (
    <section className="cf-airev" aria-label="AI review">
      <header className="cf-airev__head">
        <h4 className="cf-airev__title">What a vision model sees</h4>
        <button
          type="button"
          className="cf-airev__ask"
          disabled={ask.isPending}
          onClick={() => ask.mutate()}
        >
          {ask.isPending
            ? "Looking…"
            : review
              ? "Look again"
              : "Ask the AI to review these"}
        </button>
      </header>
      <p className="cf-airev__lead">
        It answers what the measurements cannot: whether these are the same subject in the same
        world, or whether the picture model wandered off between frames. It decides nothing — you
        still record the verdict.
      </p>

      {ask.isPending && (
        <p className="cf-airev__pending" role="status">
          The local vision model is looking at all {review?.frames.length ?? "the"} frames at once.
          About 40 seconds on this machine, and it holds the GPU while it does.
        </p>
      )}
      {problem && (
        <p className="cf-airev__problem" role="alert">
          {problem}
        </p>
      )}

      {review && (
        <>
          {!review.current && (
            <p className="cf-airev__stale" role="note">
              These frames were redrawn after this review was written, so it is about pictures that
              are no longer here. Kept because it says what was wrong last time — ask again for an
              opinion about what is on screen now.
            </p>
          )}

          <div className="cf-airev__set" data-same={review.set.same_world || undefined}>
            <p className="cf-airev__verdict">
              {review.set.same_world
                ? "Reads as one set."
                : "Does not read as one set."}
            </p>
            <p className="cf-airev__summary">{review.set.summary}</p>
            {review.set.what_changes.length > 0 && (
              <>
                <p className="cf-airev__changeslabel">What moves between the frames</p>
                <ul className="cf-airev__changes">
                  {review.set.what_changes.map((change) => (
                    <li key={change}>{change}</li>
                  ))}
                </ul>
              </>
            )}
            {review.set.drifting_frames.length > 0 && (
              <p className="cf-airev__drift">
                Left the others behind: {review.set.drifting_frames.map((id) => id).join(", ")}
              </p>
            )}
          </div>

          <ul className="cf-airev__frames">
            {review.frames.map((frame) => (
              <Opinion key={frame.frame_id} frame={frame} />
            ))}
          </ul>

          {review.flagged.length > 0 && (
            <div className="cf-airev__actions">
              <button
                type="button"
                className="cf-airev__mark"
                onClick={() => onMarkFlagged(review.flagged)}
              >
                Mark its {review.flagged.length} rejection
                {review.flagged.length === 1 ? "" : "s"} above
              </button>
              <span className="cf-airev__markhint">
                Fills in your selection. It does not record anything — you still write the reason
                and press Record verdict.
              </span>
            </div>
          )}

          <details className="cf-airev__prov">
            <summary>
              {review.model_alias} · {review.elapsed_s.toFixed(0)}s · {when(review.reviewed_at)}
            </summary>
            <p className="cf-airev__provline">
              <code>{review.model_id}</code>
            </p>
            {/* What the judge was told. Without it there is no way to tell a picture that is
                wrong from a brief that never reached the reviewer. */}
            <p className="cf-airev__provlabel">What it was told these are for</p>
            <pre className="cf-airev__intent">{review.intent}</pre>
          </details>
        </>
      )}
    </section>
  );
}
