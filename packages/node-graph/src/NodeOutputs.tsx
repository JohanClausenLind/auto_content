/**
 * What a node produced, drawn on the node.
 *
 * This is the one thing the canvas could not do. A run's files were reachable — grouped by file
 * type, in a panel over the graph — and the question an operator actually has in front of a bad
 * drawing is *which step made this, and what else did that step make?* ComfyUI answers it by
 * hanging every output off the node that emitted it, which is why a bad image there is one click
 * from the sampler that drew it, and it is the right shape for the same reason here: the drift
 * number and the eight frames it is about belong on one node.
 *
 * The strip is deliberately small. A node is 260px wide and the graph is read at 20% zoom (see
 * the screenshot this was built from — 22 nodes, all of them unreadable); four thumbnails and a
 * count is what survives that, and "open these" is a button rather than a gallery. The full
 * review — every frame at size, the voice lines playing, the film — is the host application's
 * panel, which is why this component takes `onOpen` and renders no modal of its own.
 *
 * It renders nothing at all when a node produced nothing. An empty strip under every node on the
 * canvas would cost every graph 40px a node to say nothing.
 */

import { memo } from "react";

/** One file, as much of it as a thumbnail needs. */
export interface NodeOutputItem {
  /** Fetched by the browser, authenticated by the session cookie: no blob URLs, no JS holding
   *  a 6 MB film in memory. */
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
  /**
   * Whether the run *observed* which files this step wrote, or the path table guessed.
   *
   * Shown, because the two are different claims. Every run made before per-node recording is
   * `inferred`, and a reader deciding which step to blame for a fault deserves to know that the
   * attribution is a guess from a filename.
   */
  readonly attribution: "recorded" | "inferred";
  /** Drawings at this node that nobody has decided about. The badge that finds the gate. */
  readonly awaitingReview?: number;
  /** True when this node belongs to the lane but the open run did not execute it — its files are
   *  whatever an earlier run left. Not a failure, and must not read as one. */
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
        {/* The button carries the summary rather than sitting next to it: at this size a label
            and a separate control are two things competing for one row, and the whole row is
            "open what this node made". */}
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
