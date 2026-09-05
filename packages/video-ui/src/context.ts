import type { RenderBundle } from "@content-factory/content-schema-ts";
import type { AssetUrlResolver, ContentTheme } from "@content-factory/content-ui";
import { createContext, useContext } from "react";

export interface SceneEnv {
  bundle: RenderBundle;
  theme: ContentTheme;
  /** Maps `bundle.assets[assetId]` (a local path) to a URL the host can serve — the same contract
   * `Artboard` uses. Identity by default, so tests and SSR need no Remotion host. */
  assetUrl: AssetUrlResolver;
}

export const SceneEnvContext = createContext<SceneEnv | null>(null);

export function useSceneEnv(): SceneEnv {
  const env = useContext(SceneEnvContext);
  if (!env) throw new Error("Scene components must render inside <TimelineComposition> (SceneEnvContext missing)");
  return env;
}
