import type { CompiledExplainerScene, Scene } from "@content-factory/content-schema-ts";

import type { SceneState } from "../state";

export interface TemplateProps<T> {
  compiled: CompiledExplainerScene;
  scene: Scene;
  template: T;
  state: SceneState;
}
