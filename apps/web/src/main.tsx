import "@content-factory/web-ui/styles.css";
import "@content-factory/pipeline-canvas/styles.css";
import "./shell/shell.css";
import "./pages/runs.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App, createAppQueryClient } from "./app";
import { registerServiceWorker } from "./pwa/registerSw";
import { createAppRouter } from "./router";

const queryClient = createAppQueryClient();
const router = createAppRouter(queryClient);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App router={router} queryClient={queryClient} />
  </StrictMode>,
);

registerServiceWorker();
