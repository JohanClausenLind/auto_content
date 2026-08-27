// EditorCore (principle 2.9): typed, reversible operations applied by deterministic code.
// Models propose operation batches; validators approve; this module applies them and produces
// the exact inverse batch so every edit is undoable and replayable to the same revision hash.
export * from "./document.js";
export * from "./operations.js";
export * from "./hash.js";
