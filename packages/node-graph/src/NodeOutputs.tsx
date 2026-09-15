/** What a node produced, on the node: four thumbnails and a count, and nothing if it made nothing. */

import { memo } from "react";

/** One file, as much of it as a thumbnail needs. */
export interface NodeOutputItem {
  /** Fetched by the browser under the session cookie: no blob URLs, no JS holding a 6 MB film. */
  readonly url: string;
  readonly label: string;
  readonly kind: "image" | "video" | "audio" | "text" | "data";
}

export interface NodeOutputs {
  /** How many files of each kind this node produced, including ones not listed below. */
  readonly counts: Readonly<Record<NodeOutputItem["kind"], number>>;
  /** The few worth drawing on the node itself. Images, in the order the run wrote them. */
  readonly previews: readonly NodeOutputItem[];
  readonly total: number;
  /** Whether the run observed which files this step wrote, or the path table guessed by filename. */
  readonly attribution: "recorded" | "inferred";
  /** Drawings at this node that nobody has decided about. The badge that finds the gate. */
  readonly awaitingReview?: number;
  /** In the lane but not executed by the open run, so its files are an earlier run's; not a failure. */
  readonly carried?: boolean;
}

const KIND_LABEL: Record<NodeOutputItem["kind"], (n: number) => string> = {
  image: (n) => `${n} image${n === 1 ? "" : "s"}`,
  video: (n) => `${n} video${n === 1 ? "" : "s"}`,
  audio: (n) => `${n} audio`,
  text: (n) => `${n} text`,
  data: (n) => `${n} data`,
};

const PREVIEW_LIMIT = 4;

export function summarize(counts: NodeOutputs["counts"]): string {
  const parts = (Object.keys(KIND_LABEL) as NodeOutputItem["kind"][])
    .filter((kind) => counts[kind] > 0)
    .map((kind) => KIND_LABEL[kind](counts[kind]));
  return parts.join(" · ");
}

export const NodeOutputStrip = memo(function NodeOutputStrip({
  outputs,
  nodeTitle,
  onOpen,
}: {
  readonly outputs: NodeOutputs;
  readonly nodeTitle: string;
  onOpen?(): void;
}) {
  if (outputs.total === 0) return null;
  const shown = outputs.previews.slice(0, PREVIEW_LIMIT);
  const more = outputs.total - shown.length;
  return (
    <div className="ng-outputs nodrag" data-carried={outputs.carried || undefined}>
      {shown.length > 0 && (
        <div className="ng-outputs__strip">
          {shown.map((item) => (
            <img
              key={item.url}
              className="ng-outputs__thumb"
              src={item.url}
              alt={item.label}
              title={item.label}
              loading="lazy"
              draggable={false}
            />
          ))}
          {more > 0 && <span className="ng-outputs__more">+{more}</span>}
        </div>
      )}
      <div className="ng-outputs__foot">
        {/* The button carries the summary rather than sitting next to it: at this size a label and
           a separate control are two things competing for one row. */}
        <button
          type="button"
          className="ng-outputs__open"
          onClick={onOpen}
          disabled={!onOpen}
          title={
            outputs.attribution === "recorded"
              ? `${outputs.total} file(s) this run recorded at ${nodeTitle}`
              : `${outputs.total} file(s) attributed to ${nodeTitle} by their path — this run did not record which step wrote them`
          }
        >
          {summarize(outputs.counts) || `${outputs.total} files`}
          {outputs.attribution === "inferred" && <span className="ng-outputs__guess">?</span>}
        </button>
        {outputs.awaitingReview ? (
          <span className="ng-outputs__badge">{outputs.awaitingReview} to review</span>
        ) : null}
      </div>
    </div>
  );
});
