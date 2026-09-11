/**
 * One node's output, at a size you can actually judge it at.
 *
 * The canvas draws four thumbnails and a count on the node; this is what the count opens. It is
 * per *node* rather than per run, which is the whole point: a run's files grouped by file type
 * answer "what came out of this", and the question in front of a bad drawing is "which step made
 * this, and what else did that step make" — with the step's own facts beside it, because
 * "3 attempts, backend hidream-o1, drift 0.849" is the first thing to know about a frame that
 * came out wrong.
 *
 * Everything plays here rather than downloading. The session is a cookie, so `<img src>`,
 * `<video src>` and `<audio src>` authenticate themselves and the browser does the streaming and
 * the caching — no blob URLs, no JS holding a 6 MB film in memory. Voice lines are a list of
 * players and not one merged track, because a bad take is one line, and the line is what gets
 * re-recorded.
 */

import { useMemo, useState } from "react";
import { api } from "../api/client";
import type { OutputKind, RunOutput, RunStep } from "../api/types";
import { formatBytes } from "./runFormat";

/** Intermediate output: kept, because a control map is exactly what you want when a drawing came
 *  out wrong, and folded, so it cannot bury the six pictures that are the actual result. */
const DEBUG_ROLES = new Set(["control", "anchor-upscaled", "render", "input", "marker"]);

const KIND_TITLE: Record<OutputKind, string> = {
  video: "Video",
  image: "Images",
  audio: "Audio",
  text: "Text",
  data: "Data",
};

/** The stage's own numbers, as a row of pills. Rendered from whatever the stage recorded rather
 *  than from a known list: every stage's facts are its own, and a panel that only knew about the
 *  four it was written against would silently drop the one that mattered. */
function Facts({ facts }: { facts: Record<string, unknown> }) {
  const entries = Object.entries(facts).filter(([, value]) => value !== null && value !== "");
  if (entries.length === 0) return null;
  return (
    <ul className="cf-nodeout__facts">
      {entries.map(([key, value]) => (
        <li key={key}>
          <span className="cf-nodeout__factkey">{key.replaceAll("_", " ")}</span>
          <span className="cf-nodeout__factval">
            {typeof value === "object" ? JSON.stringify(value) : String(value)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function name(path: string): string {
  return path.split("/").pop() ?? path;
}

export function NodeOutputPane({
  runId,
  nodeTitle,
  step,
  files,
  onClose,
}: {
  runId: string;
  nodeTitle: string;
  step: RunStep;
  files: readonly RunOutput[];
  onClose(): void;
}) {
  const [showDebug, setShowDebug] = useState(false);
  const src = (file: RunOutput) => api.history.fileUrl(runId, file.path);

  const groups = useMemo(() => {
    const shown = files.filter((f) => showDebug || !DEBUG_ROLES.has(f.role));
    const by = (kind: OutputKind) => shown.filter((f) => f.kind === kind);
    return {
      video: by("video"),
      image: by("image"),
      audio: by("audio"),
      text: by("text"),
      data: by("data"),
    };
  }, [files, showDebug]);
  const debugCount = files.filter((f) => DEBUG_ROLES.has(f.role)).length;

  return (
    <section className="cf-nodeout" aria-label={`Output of ${nodeTitle}`}>
      <header className="cf-nodeout__head">
        <div>
          <h3 className="cf-nodeout__title">{nodeTitle}</h3>
          <p className="cf-nodeout__meta">
            <code>{step.stage}</code>
            {step.pinned && <span className="cf-nodeout__tag">pinned — frozen, not run</span>}
            {!step.ran && <span className="cf-nodeout__tag">not run by this run</span>}
            {step.blocked && <span className="cf-nodeout__tag" data-tone="wait">stopped for a person</span>}
            {step.ran && !step.ok && !step.blocked && (
              <span className="cf-nodeout__tag" data-tone="bad">failed</span>
            )}
            {step.seconds > 0 && <span>{step.seconds.toFixed(1)}s</span>}
            <span>
              {step.outputs_total} file{step.outputs_total === 1 ? "" : "s"}
            </span>
          </p>
        </div>
        <button type="button" className="cf-nodeout__close" aria-label="Close node output" onClick={onClose}>
          ✕
        </button>
      </header>

      {step.error && <p className="cf-nodeout__error">{step.error}</p>}
      {/* Said plainly rather than in a tooltip: a reader about to blame this step for a fault has
          to know that the attribution is a guess from a filename. */}
      {step.attribution === "inferred" && (
        <p className="cf-nodeout__hint">
          This run did not record which step wrote which file. These are attributed to{" "}
          <code>{step.stage}</code> by their path — runs made before 2026-09-11 have no per-node
          record, and a newer run of this lane will.
        </p>
      )}
      <Facts facts={step.facts} />

      {files.length === 0 ? (
        <p className="cf-nodeout__empty">
          {step.ran
            ? "This step produced no files that can be shown."
            : "This run did not execute this step, and nothing it produced earlier is still here."}
        </p>
      ) : (
        <div className="cf-nodeout__groups">
          {groups.video.length > 0 && (
            <section>
              <h4 className="cf-nodeout__grouptitle">{KIND_TITLE.video} ({groups.video.length})</h4>
              {groups.video.map((file) => (
                <figure key={file.path} className="cf-nodeout__video">
                  {/* preload=metadata: a node can hold four films and opening it must not pull
                      20 MB. The poster frame and the duration are enough to choose one. */}
                  {/* eslint-disable-next-line jsx-a11y/media-has-caption -- the captions are a
                      separate .srt under Text; there is no WebVTT track to attach. */}
                  <video controls preload="metadata" src={src(file)} />
                  <figcaption>
                    {name(file.path)} · {formatBytes(file.bytes)}
                  </figcaption>
                </figure>
              ))}
            </section>
          )}

          {groups.image.length > 0 && (
            <section>
              <h4 className="cf-nodeout__grouptitle">{KIND_TITLE.image} ({groups.image.length})</h4>
              <ul className="cf-nodeout__grid">
                {groups.image.map((file) => (
                  <li key={file.path}>
                    {/* A new tab is the full-size view: the browser already has an image viewer
                        and a hand-built lightbox would be a worse one. */}
                    <a
                      href={src(file)}
                      target="_blank"
                      rel="noreferrer"
                      title={`${file.path} · ${formatBytes(file.bytes)}`}
                    >
                      <img src={src(file)} alt={file.path} loading="lazy" />
                    </a>
                    <span className="cf-nodeout__caption">{name(file.path)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {groups.audio.length > 0 && (
            <section>
              <h4 className="cf-nodeout__grouptitle">{KIND_TITLE.audio} ({groups.audio.length})</h4>
              <ul className="cf-nodeout__audio">
                {groups.audio.map((file) => (
                  <li key={file.path}>
                    <span className="cf-nodeout__filename">{name(file.path)}</span>
                    {/* eslint-disable-next-line jsx-a11y/media-has-caption -- one narration take;
                        its transcript is the script listed under Text. */}
                    <audio controls preload="none" src={src(file)} />
                  </li>
                ))}
              </ul>
            </section>
          )}

          {(groups.text.length > 0 || groups.data.length > 0) && (
            <section>
              <h4 className="cf-nodeout__grouptitle">
                Text and data ({groups.text.length + groups.data.length})
              </h4>
              <ul className="cf-nodeout__files">
                {[...groups.text, ...groups.data].map((file) => (
                  <li key={file.path}>
                    <a href={src(file)} target="_blank" rel="noreferrer">
                      {file.path}
                    </a>
                    <span className="cf-nodeout__size">{formatBytes(file.bytes)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {debugCount > 0 && (
            <button
              type="button"
              className="cf-nodeout__debugtoggle"
              aria-pressed={showDebug}
              onClick={() => setShowDebug((v) => !v)}
            >
              {showDebug ? "Hide" : "Show"} {debugCount} intermediate file
              {debugCount === 1 ? "" : "s"} (control maps, upscales, done-markers)
            </button>
          )}
        </div>
      )}
    </section>
  );
}
