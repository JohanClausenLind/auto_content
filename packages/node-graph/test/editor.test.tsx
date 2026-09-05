import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it } from "vitest";
import { NodeGraphEditor } from "../src/NodeGraphEditor";
import { PropertiesPanel } from "../src/PropertiesPanel";
import { useGraphEditor, type GraphEditor } from "../src/useGraphEditor";
import { applyOps, connectOps, emptyGraph, makeNode, type WorkspaceGraph } from "../src/graphModel";
import { catalog } from "./fixtures";

function seededGraph(): WorkspaceGraph {
  let graph = emptyGraph("t1", "Test graph");
  graph = applyOps(graph, [
    { op: "add_node", node: makeNode(catalog, "test.brief", { id: "b", x: 0, y: 0, values: { topic: "cats" } }) },
    { op: "add_node", node: makeNode(catalog, "test.script", { id: "s", x: 260, y: 0 }) },
  ]).graph;
  return applyOps(graph, connectOps(graph, { node: "b", slot: "brief" }, { node: "s", slot: "brief" })).graph;
}

function Harness({
  graph,
  onEditor,
  withProps = false,
}: {
  graph: WorkspaceGraph;
  onEditor?: (editor: GraphEditor) => void;
  withProps?: boolean;
}) {
  const editor = useGraphEditor(catalog, { initialGraph: graph });
  useEffect(() => {
    onEditor?.(editor);
  });
  return (
    <div style={{ width: 800, height: 600 }}>
      <NodeGraphEditor editor={editor} aria-label="Test canvas" />
      {withProps && <PropertiesPanel editor={editor} />}
    </div>
  );
}

describe("NodeGraphEditor", () => {
  it("renders nodes with titles, slots, widgets and the note field", async () => {
    render(<Harness graph={seededGraph()} />);
    expect(await screen.findByText("Brief")).toBeInTheDocument();
    expect(screen.getByText("Write Script")).toBeInTheDocument();
    // slot labels
    expect(screen.getAllByText("brief").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("script")).toBeInTheDocument();
    // widgets are real labelled controls
    expect(screen.getByLabelText("topic")).toHaveValue("cats");
    expect(screen.getByLabelText("tone")).toHaveValue("neutral");
    expect(screen.getByLabelText("length")).toHaveValue(60);
    // every node takes a note underneath
    expect(screen.getByLabelText("Note for Brief")).toBeInTheDocument();
    expect(screen.getByLabelText("Note for Write Script")).toBeInTheDocument();
  });

  it("typing a note stores it on the node", async () => {
    const user = userEvent.setup();
    let editor: GraphEditor | undefined;
    render(<Harness graph={seededGraph()} onEditor={(e) => (editor = e)} />);
    const note = await screen.findByLabelText("Note for Brief");
    await user.type(note, "under the node");
    expect(editor?.graph.nodes.find((n) => n.id === "b")?.note).toBe("under the node");
  });

  it("combo arrows cycle options and commit through the model", async () => {
    const user = userEvent.setup();
    let editor: GraphEditor | undefined;
    render(<Harness graph={seededGraph()} onEditor={(e) => (editor = e)} />);
    await user.click(await screen.findByRole("button", { name: "Next tone" }));
    expect(editor?.graph.nodes.find((n) => n.id === "b")?.values.tone).toBe("playful");
    await user.click(screen.getByRole("button", { name: "Previous tone" }));
    expect(editor?.graph.nodes.find((n) => n.id === "b")?.values.tone).toBe("neutral");
  });

  it("collapse hides the body and undo brings it back", async () => {
    const user = userEvent.setup();
    let editor: GraphEditor | undefined;
    render(<Harness graph={seededGraph()} onEditor={(e) => (editor = e)} />);
    const collapse = (await screen.findAllByRole("button", { name: "Collapse node" }))[0]!;
    await user.click(collapse);
    expect(editor?.graph.nodes.some((n) => n.collapsed)).toBe(true);
    await act(async () => editor?.undo());
    expect(editor?.graph.nodes.some((n) => n.collapsed)).toBe(false);
  });

  it("shows canvas stats and marks the graph valid/invalid", async () => {
    render(<Harness graph={seededGraph()} />);
    expect(await screen.findByText("N: 2")).toBeInTheDocument();
    expect(screen.getByText("L: 1")).toBeInTheDocument();
    // script output unused is only a warning; graph counts as valid
    expect(screen.getByText("✓ valid")).toBeInTheDocument();
  });

  it("addConnectedNode adds and wires in one undoable step", async () => {
    let editor: GraphEditor | undefined;
    render(<Harness graph={seededGraph()} onEditor={(e) => (editor = e)} />);
    await screen.findByText("Brief");
    await act(async () => {
      editor?.addConnectedNode("test.video", { x: 500, y: 0 }, { node: "s", slot: "script" });
    });
    expect(editor?.graph.nodes).toHaveLength(3);
    const link = editor?.graph.links.find((l) => l.from_node === "s" && l.from_slot === "script");
    expect(link?.to_slot).toBe("script");
    await act(async () => editor?.undo());
    expect(editor?.graph.nodes).toHaveLength(2);
    expect(editor?.graph.links).toHaveLength(1);
  });

  it("read-only canvas disables editing affordances", async () => {
    function ReadOnly() {
      const editor = useGraphEditor(catalog, { initialGraph: seededGraph() });
      return <NodeGraphEditor editor={editor} readOnly />;
    }
    render(<ReadOnly />);
    const topic = await screen.findByLabelText("topic");
    expect(topic).toBeDisabled();
  });
});

describe("PropertiesPanel", () => {
  it("lists nodes in execution order and selecting shows parameters", async () => {
    const user = userEvent.setup();
    render(<Harness graph={seededGraph()} withProps />);
    await user.click(await screen.findByRole("tab", { name: "Nodes" }));
    const list = screen.getByRole("list", { name: "Nodes in execution order" });
    const rows = within(list).getAllByRole("button");
    expect(rows.map((r) => r.textContent)).toEqual(["Brief", "Write Script"]);

    await user.click(rows[1]!);
    await user.click(screen.getByRole("tab", { name: "Parameters" }));
    expect(screen.getByDisplayValue("Write Script")).toBeInTheDocument();
  });
});

describe("node context menu", () => {
  it("right-click opens the menu; mute and delete route through the editor", async () => {
    const user = userEvent.setup();
    let editor: GraphEditor | undefined;
    render(<Harness graph={seededGraph()} onEditor={(e) => (editor = e)} />);
    const node = await screen.findByTestId("rf__node-s");
    await user.pointer({ keys: "[MouseRight]", target: node });
    const menu = await screen.findByRole("menu", { name: /Write Script/ });
    await user.click(within(menu).getByRole("menuitem", { name: "Mute" }));
    expect(editor?.graph.nodes.find((n) => n.id === "s")?.mode).toBe("muted");

    await user.pointer({ keys: "[MouseRight]", target: node });
    const again = await screen.findByRole("menu", { name: /Write Script/ });
    await user.click(within(again).getByRole("menuitem", { name: /Delete/ }));
    expect(editor?.graph.nodes.some((n) => n.id === "s")).toBe(false);
    expect(editor?.graph.links).toHaveLength(0);
  });
});
