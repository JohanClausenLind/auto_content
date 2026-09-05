/**
 * Dropping a file on the canvas.
 *
 * The drop does the work an operator would otherwise do by hand: the file is uploaded and
 * identified from its bytes (never its name), the node that can hold it appears where it was
 * dropped with the stored asset already on it, and the strip below offers the next steps that
 * are actually worth taking on *this* file. A container the pipeline cannot read (a Matroska
 * screen recording, an Opus voice memo) is converted on the way in, and the strip says what was
 * done — the node holds an MP4, not the .mkv that was dropped — with the reason attached, drawn from what was
 * measured. Clicking one spawns that node already wired.
 *
 * Everything specific lives on the server: which node type holds which kind of file, and which
 * suggestions are type-correct, come back with the upload, so the canvas cannot offer a step
 * that produces a link the graph would refuse.
 */

import { useCallback, useState } from "react";
import type { GraphEditor } from "@content-factory/node-graph";
import { api, isApiError } from "../api/client";
import type { UploadedDrop } from "../api/types";

export interface DropState {
  /** One entry per dropped file, newest first. */
  readonly drops: readonly DroppedNode[];
  readonly busy: number;
  readonly errors: readonly string[];
  onDropFiles(files: readonly File[], position: { x: number; y: number }): void;
  dismiss(nodeId: string): void;
  /** Record that a suggestion has been spawned, so the strip stops offering it. */
  markTaken(nodeId: string, nodeType: string): void;
  /** Forget every drop — the nodes they made belong to a graph that is no longer open. */
  reset(): void;
  clearErrors(): void;
}

export interface DroppedNode {
  readonly nodeId: string;
  readonly drop: UploadedDrop;
  /** Suggestions already taken, so the strip stops offering them. */
  readonly taken: readonly string[];
}

/** Two dropped files must not land on top of each other. */
const STACK_OFFSET = 40;

/**
 * Where the node goes. The canvas resolves the drop point through React Flow, which needs a
 * measured container to do it — before one exists (a canvas that has not been laid out yet) the
 * answer is NaN, and a node at NaN is a node nobody can see or serialise.
 */
function landing(position: { x: number; y: number }, index: number): { x: number; y: number } {
  const usable = Number.isFinite(position.x) && Number.isFinite(position.y);
  const base = usable ? position : { x: 120, y: 120 };
  return { x: base.x + index * STACK_OFFSET, y: base.y + index * STACK_OFFSET };
}

export function useFileDrops(editor: GraphEditor): DropState {
  const [drops, setDrops] = useState<readonly DroppedNode[]>([]);
  const [busy, setBusy] = useState(0);
  const [errors, setErrors] = useState<readonly string[]>([]);

  const onDropFiles = useCallback(
    (files: readonly File[], position: { x: number; y: number }) => {
      files.forEach((file, index) => {
        setBusy((n) => n + 1);
        void api.uploads
          .create(file)
          .then((drop) => {
            const at = landing(position, index);
            const values =
              drop.node_slot === "sources"
                ? {}
                : { asset: drop.asset_id, filename: drop.filename, bytes: drop.size_bytes };
            const nodeId = editor.addNode(drop.node_type, at, values);
            if (drop.node_type === "ingest") {
              // Ingest has no widget for a file: it reads the whole uploads folder. Say on the
              // node which file put it there, or the graph loses that fact entirely.
              editor.setNote(nodeId, `Staged: ${drop.filename} (${drop.description})`);
            }
            setDrops((current) => [{ nodeId, drop, taken: [] }, ...current]);
          })
          .catch((error: unknown) => {
            const detail = isApiError(error)
              ? error.detail
              : error instanceof Error
                ? error.message
                : String(error);
            setErrors((current) => [`${file.name}: ${detail}`, ...current].slice(0, 4));
          })
          .finally(() => setBusy((n) => n - 1));
      });
    },
    [editor],
  );

  const dismiss = useCallback(
    (nodeId: string) => setDrops((current) => current.filter((d) => d.nodeId !== nodeId)),
    [],
  );

  const markTaken = useCallback((nodeId: string, nodeType: string) => {
    setDrops((current) =>
      current.map((entry) =>
        entry.nodeId === nodeId && !entry.taken.includes(nodeType)
          ? { ...entry, taken: [...entry.taken, nodeType] }
          : entry,
      ),
    );
  }, []);

  const reset = useCallback(() => {
    setDrops([]);
    setErrors([]);
  }, []);

  return {
    drops,
    busy,
    errors,
    onDropFiles,
    dismiss,
    markTaken,
    reset,
    clearErrors: useCallback(() => setErrors([]), []),
  };
}

export function DropSuggestions({ state, editor }: { state: DropState; editor: GraphEditor }) {
  const [refusal, setRefusal] = useState<string | null>(null);
  if (state.drops.length === 0 && state.errors.length === 0 && state.busy === 0) return null;

  const spawn = (entry: DroppedNode, index: number) => {
    const suggestion = entry.drop.suggestions[index];
    if (!suggestion) return;
    const source = editor.graph.nodes.find((n) => n.id === entry.nodeId);
    if (!source) return;
    // Stack each added step below the last one instead of on top of it.
    const at = landing({ x: source.x + 360, y: source.y + entry.taken.length * 160 }, 0);
    if (!suggestion.to_slot) {
      // A step that reads the run's uploads folder itself: it belongs on the canvas, but there
      // is no link to draw, and drawing one anyway would be a lie about where its input is.
      editor.addNode(suggestion.node_type, at, suggestion.values);
      state.markTaken(entry.nodeId, suggestion.node_type);
      return;
    }
    // Added and wired in one commit: connecting afterwards would ask this render's graph about
    // a node it has not seen yet, which is exactly the "unknown node" refusal.
    const spawned = editor.addConnectedNode(
      suggestion.node_type,
      at,
      { node: entry.nodeId, slot: entry.drop.node_slot },
      { values: suggestion.values, toSlot: suggestion.to_slot },
    );
    if (spawned === null) {
      setRefusal(`${suggestion.node_type} is not a node type this canvas knows`);
      return;
    }
    const wired = editor.graph.links.some((link) => link.to_node === spawned);
    setRefusal(
      wired ? null : `${suggestion.title} was added, but nothing could be wired into it`,
    );
    state.markTaken(entry.nodeId, suggestion.node_type);
  };

  return (
    <section className="cf-drop" aria-label="Dropped files">
      {state.busy > 0 && (
        <p className="cf-drop__busy" role="status">
          Uploading {state.busy} file{state.busy === 1 ? "" : "s"}… (a container the pipeline
          cannot read is converted first)
        </p>
      )}
      {state.errors.map((error, index) => (
        <p key={index} className="cf-drop__error" role="alert">
          {error}
        </p>
      ))}
      {refusal && <p className="cf-drop__error">{refusal}</p>}
      {state.drops.map((entry) => (
        <article key={entry.nodeId} className="cf-drop__card" aria-label={`Dropped ${entry.drop.filename}`}>
          <header className="cf-drop__head">
            <span className="cf-drop__name">{entry.drop.filename}</span>
            <span className="cf-drop__what">{entry.drop.description}</span>
            {entry.drop.conversion && (
              // The file on the canvas is not the file they dropped, so say so rather than
              // letting them discover it in the node's filename.
              <span className="cf-drop__converted">
                {`converted to ${entry.drop.filename.split(".").pop()}: ${entry.drop.conversion.detail}`}
              </span>
            )}
            <button
              type="button"
              className="cf-drop__close"
              aria-label={`Dismiss ${entry.drop.filename}`}
              onClick={() => state.dismiss(entry.nodeId)}
            >
              ✕
            </button>
          </header>
          <ul className="cf-drop__list">
            {entry.drop.suggestions.map((suggestion, index) => (
              <li key={suggestion.node_type}>
                <button
                  type="button"
                  className="cf-button cf-button--secondary cf-button--sm"
                  disabled={entry.taken.includes(suggestion.node_type)}
                  onClick={() => spawn(entry, index)}
                >
                  {entry.taken.includes(suggestion.node_type) ? "Added" : suggestion.title}
                </button>
                <span className="cf-drop__why">{suggestion.why}</span>
              </li>
            ))}
          </ul>
        </article>
      ))}
    </section>
  );
}
