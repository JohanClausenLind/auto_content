/**
 * One run, opened over the canvas: what it produced, and what it is waiting for.
 *
 * The run list stays on the left while this is open, so switching from one run to the next is one
 * click and never a navigation — the point of putting all of it in the workspace was that
 * reviewing a batch of drawings should not mean leaving the page that made them.
 *
 * The gate comes first when there is one. A run parked at `review_frames` has its drawings
 * finished and nothing else to give until somebody looks, so the review is the top of the pane and
 * the files are underneath it.
 *
 * Media plays here rather than downloading, because the point is to look at it. The session is a
 * cookie, so `<img src>` and `<video src>` authenticate themselves and the browser does the
 * streaming and caching — no blob URLs, no JS holding a 6 MB film in memory.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { historyRunQuery, runReviewQuery } from "../api/queries";
import type { OutputKind, RunOutput } from "../api/types";
import { FrameReview } from "./FrameReview";
import { formatBytes, formatCost, formatExact, OUTCOME_LABEL, OUTCOME_TONE, runLabel } from "./runFormat";

/** Debug and intermediate output: kept, because it is what you want when a drawing came out
 * wrong, but folded away so it cannot bury the six pictures that are the actual result. */
const DEBUG_ROLES = new Set(["control", "anchor-upscaled", "render", "input", "marker"]);

function OutputGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="cf-hist__group">
      <h4 className="cf-hist__grouptitle">{title}</h4>
      {children}
    </section>
  );
}

function Outputs({ runId, outputs }: { runId: string; outputs: readonly RunOutput[] }) {
  const [showDebug, setShowDebug] = useState(false);
  const src = (o: RunOutput) => api.history.fileUrl(runId, o.path);

  const groups = useMemo(() => {
    const shown = outputs.filter((o) => showDebug || !DEBUG_ROLES.has(o.role));
    const by = (kind: OutputKind) => shown.filter((o) => o.kind === kind);
    return { video: by("video"), image: by("image"), audio: by("audio"), text: by("text"), data: by("data") };
  }, [outputs, showDebug]);

  const debugCount = outputs.filter((o) => DEBUG_ROLES.has(o.role)).length;

  return (
    <div className="cf-hist__outputs">
      {groups.video.length > 0 && (
        <OutputGroup title={`Video (${groups.video.length})`}>
          {groups.video.map((o) => (
            <figure key={o.path} className="cf-hist__video">
              {/* preload=metadata: a run can hold four videos and this panel must not pull 20 MB
                  to draw a list. The poster frame and duration are enough to choose one. */}
              {/* eslint-disable-next-line jsx-a11y/media-has-caption -- the run's captions are a
                  separate .srt in "Text and data"; there is no WebVTT track to attach. */}
              <video controls preload="metadata" src={src(o)} />
              <figcaption>
                {o.path.split("/").pop()} · {formatBytes(o.bytes)}
              </figcaption>
            </figure>
          ))}
        </OutputGroup>
      )}

      {groups.image.length > 0 && (
        <OutputGroup title={`Images (${groups.image.length})`}>
          <ul className="cf-hist__grid">
            {groups.image.map((o) => (
              <li key={o.path}>
                {/* A new tab is the full-size view: the browser already has an image viewer and
                    a hand-built lightbox would be a worse one. */}
                <a href={src(o)} target="_blank" rel="noreferrer" title={`${o.path} · ${formatBytes(o.bytes)}`}>
                  <img src={src(o)} alt={o.path} loading="lazy" />
                </a>
              </li>
            ))}
          </ul>
        </OutputGroup>
      )}

      {groups.audio.length > 0 && (
        <OutputGroup title={`Audio (${groups.audio.length})`}>
          <ul className="cf-hist__audio">
            {groups.audio.map((o) => (
              <li key={o.path}>
                <span className="cf-hist__filename">{o.path.split("/").pop()}</span>
                {/* eslint-disable-next-line jsx-a11y/media-has-caption -- a narration take, whose
                    transcript is the script and captions listed below it. */}
                <audio controls preload="none" src={src(o)} />
              </li>
            ))}
          </ul>
        </OutputGroup>
      )}

      {(groups.text.length > 0 || groups.data.length > 0) && (
        <OutputGroup title={`Text and data (${groups.text.length + groups.data.length})`}>
          <ul className="cf-hist__files">
            {[...groups.text, ...groups.data].map((o) => (
              <li key={o.path}>
                <a href={src(o)} target="_blank" rel="noreferrer">
                  {o.path}
                </a>
                <span className="cf-hist__size">{formatBytes(o.bytes)}</span>
              </li>
            ))}
          </ul>
        </OutputGroup>
      )}

      {debugCount > 0 && (
        <button type="button" className="cf-hist__debugtoggle" aria-pressed={showDebug} onClick={() => setShowDebug((v) => !v)}>
          {showDebug ? "Hide" : "Show"} {debugCount} intermediate file{debugCount === 1 ? "" : "s"} (control maps, upscales, done-markers)
        </button>
      )}
    </div>
  );
}

export function RunPane({ runId, onClose }: { runId: string; onClose(): void }) {
  const run = useQuery(historyRunQuery(runId));
  const review = useQuery(runReviewQuery(runId));
  const gates = review.data?.reviews ?? [];

  return (
    <section className="cf-runpane" aria-label="Run detail">
      <header className="cf-runpane__head">
        <div className="cf-runpane__title">
          <h2>{run.data ? runLabel(run.data) : runLabel({ run_id: runId })}</h2>
          {run.data && (
            <span className="cf-hist__rowstate" data-tone={OUTCOME_TONE[run.data.outcome] ?? "wait"}>
              {OUTCOME_LABEL[run.data.outcome] ?? run.data.outcome}
            </span>
          )}
        </div>
        <button type="button" className="cf-runpane__close" aria-label="Close run" onClick={onClose}>
          ✕
        </button>
        {run.data && (
          <>
            {/* What it was rendering, above the lane and the counts: it is the thing that says
                which run this is, where "audio-picture-story, 12/12 stages" says which kind. */}
            {run.data.subject && <p className="cf-hist__detailsubject">{run.data.subject}</p>}
            <p className="cf-hist__detailmeta">
              {run.data.workflow ?? "unknown lane"} · {run.data.stages_ok}/{run.data.stages} stages
              {run.data.seconds > 0 ? ` · took ${formatCost(run.data.seconds)}` : ""} ·{" "}
              <time dateTime={new Date(run.data.finished_at * 1000).toISOString()}>
                {formatExact(run.data.finished_at)}
              </time>
              {run.data.blocked_at ? ` · blocked at ${run.data.blocked_at}` : ""}
            </p>
            <p className="cf-hist__detailpath">
              <code>{run.data.project_dir}</code>
            </p>
          </>
        )}
      </header>

      <div className="cf-runpane__body">
        {/* The gate first: a run parked here has nothing else to give until somebody looks. */}
        {gates.map((gate) => (
          <FrameReview
            key={`${runId}:${gate.deliverable}`}
            runId={runId}
            review={gate}
            resumeCommand={review.data?.resume_command ?? ""}
            aiReview={review.data?.ai_reviews?.[gate.deliverable]}
          />
        ))}

        {run.isPending ? (
          <p className="cf-hist__empty">Reading the run…</p>
        ) : run.isError ? (
          <p className="cf-hist__empty">Couldn&apos;t read this run.</p>
        ) : run.data.outputs.length === 0 ? (
          <p className="cf-hist__empty">This run produced no files that can be shown.</p>
        ) : (
          <Outputs runId={runId} outputs={run.data.outputs} />
        )}
        {run.data && run.data.outputs_total > run.data.outputs.length && (
          <p className="cf-hist__note">
            Showing {run.data.outputs.length} of {run.data.outputs_total} files.
          </p>
        )}
      </div>
    </section>
  );
}
