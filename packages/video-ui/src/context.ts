import type { RenderBundle } from "@content-factory/content-schema-ts";
import type { ContentTheme } from "@content-factory/content-ui";
import { createContext, useContext } from "react";

export interface SceneEnv {
  bundle: RenderBundle;
  theme: ContentTheme;
}

export const SceneEnvContext = createContext<SceneEnv | null>(null);

export function useSceneEnv(): SceneEnv {
  const env = useContext(SceneEnvContext);
  if (!env) throw new Error("Scene components must render inside <TimelineComposition> (SceneEnvContext missing)");
  return env;
}
