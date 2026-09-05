/**
 * Nodes are rendered by React Flow, which owns their props. Rather than threading a dozen
 * callbacks through node data (and re-rendering every node whenever one of them changes), the
 * editor hands itself down through context and nodes read what they need.
 */

import { createContext, useContext } from "react";
import type { GraphEditor } from "./useGraphEditor";

export interface EditorContextValue {
  readonly editor: GraphEditor;
  /** A read-only canvas shows the same nodes but refuses every edit. */
  readonly readOnly: boolean;
  /** Where a link is currently being dragged from, so compatible inputs can light up. */
  readonly connecting: { readonly node: string; readonly slot: string; readonly type: string } | null;
  /** Last refusal to show near the pointer ("VIDEO does not fit SCRIPT"). */
  readonly refusal: string | null;
}

export const EditorContext = createContext<EditorContextValue | null>(null);

export function useEditorContext(): EditorContextValue {
  const value = useContext(EditorContext);
  if (!value) throw new Error("useEditorContext must be used inside a NodeGraphEditor");
  return value;
}
