import { createCatalog, type NodeDefinition } from "../src/nodeDefs";

/** A tiny but structurally complete catalogue: source -> transform -> sink, plus a note. */
export const DEFS: readonly NodeDefinition[] = [
  {
    type: "test.brief",
    title: "Brief",
    category: "input",
    summary: "The idea everything hangs off",
    inputs: [],
    outputs: [{ name: "brief", type: "BRIEF" }],
    widgets: [
      { name: "topic", kind: "textarea", default: "", placeholder: "What is this about?", required: true },
      { name: "tone", kind: "combo", default: "neutral", options: ["neutral", "playful", "formal"] },
    ],
  },
  {
    type: "test.script",
    title: "Write Script",
    category: "text",
    summary: "Drafts a script from the brief",
    executor: "ai",
    inputs: [{ name: "brief", type: "BRIEF" }],
    outputs: [{ name: "script", type: "SCRIPT" }],
    widgets: [
      { name: "length", kind: "int", default: 60, min: 10, max: 600, step: 10 },
      { name: "seed", kind: "seed", default: 7 },
      { name: "strict", kind: "toggle", default: false },
    ],
  },
  {
    type: "test.video",
    title: "Render Video",
    category: "video",
    summary: "Renders the script into a clip",
    inputs: [
      { name: "script", type: "SCRIPT" },
      { name: "image", type: "IMAGE,SEQUENCE", optional: true },
    ],
    outputs: [{ name: "video", type: "VIDEO" }],
    widgets: [{ name: "fps", kind: "float", default: 30, min: 1, max: 120, precision: 2 }],
  },
  {
    type: "test.save",
    title: "Save Output",
    category: "output",
    summary: "Writes the result into the project",
    inputs: [{ name: "video", type: "VIDEO" }],
    outputs: [],
    widgets: [{ name: "prefix", kind: "text", default: "out" }],
  },
  {
    type: "test.remix",
    title: "Remix",
    category: "video",
    summary: "Turns a clip back into a draft",
    inputs: [{ name: "video", type: "VIDEO" }],
    outputs: [{ name: "draft", type: "SCRIPT" }],
    widgets: [],
  },
  {
    type: "test.note",
    title: "Note",
    category: "utility",
    summary: "Free text on the canvas",
    kind: "note",
    inputs: [],
    outputs: [],
    widgets: [],
  },
];

export const catalog = createCatalog(DEFS);
