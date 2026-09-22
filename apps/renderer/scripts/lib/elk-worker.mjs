// ELK off the main thread: takes {id, graph, layoutOptions}, answers {id, ok, graph} or {id, ok, error}.
import { parentPort } from "node:worker_threads";

import ELK from "elkjs/lib/elk.bundled.js";

const elk = new ELK();

parentPort.on("message", ({ id, graph, layoutOptions }) => {
  elk.layout(graph, { layoutOptions }).then(
    (laid) => parentPort.postMessage({ id, ok: true, graph: laid }),
    (err) => parentPort.postMessage({ id, ok: false, error: err instanceof Error ? err.message : String(err) }),
  );
});
