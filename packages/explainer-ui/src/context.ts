// What every template reads from the bundle once, indexed by id, plus the canvas scale.
import type { AssetRef, DiagramLayout, Entity, EvidenceDataset, ExplainerRenderBundle, Scene } from "@content-factory/content-schema-ts";
import { createContext, useContext } from "react";

import { scaleFor } from "./geometry";

export interface SceneEnv {
  bundle: ExplainerRenderBundle;
  /** Canvas height over the 1080 reference; token sizes multiply by it. */
  scale: number;
  scenes: ReadonlyMap<string, Scene>;
  datasets: ReadonlyMap<string, EvidenceDataset>;
  layouts: ReadonlyMap<string, DiagramLayout>;
  entities: ReadonlyMap<string, Entity>;
  assets: ReadonlyMap<string, AssetRef>;
}

export const SceneEnvContext = createContext<SceneEnv | null>(null);

export function useSceneEnv(): SceneEnv {
  const env = useContext(SceneEnvContext);
  if (!env) throw new Error("Explainer templates render inside <ExplainerComposition> (SceneEnvContext missing)");
  return env;
}

export function buildSceneEnv(bundle: ExplainerRenderBundle): SceneEnv {
  return {
    bundle,
    scale: scaleFor(bundle.timeline.height),
    scenes: new Map(bundle.spec.scenes.map((s) => [s.scene_id, s] as const)),
    datasets: new Map(bundle.datasets.map((d) => [d.dataset_id, d] as const)),
    layouts: new Map(bundle.layouts.map((l) => [l.scene_id, l] as const)),
    entities: new Map(bundle.spec.entities.map((e) => [e.entity_id, e] as const)),
    assets: new Map(bundle.spec.assets.map((a) => [a.asset_id, a] as const)),
  };
}
