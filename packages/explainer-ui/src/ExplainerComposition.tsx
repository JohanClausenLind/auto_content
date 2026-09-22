// One <Sequence> per compiled scene over the canvas surface; every frame is (bundle, frame) only.
import type { CompiledExplainerScene } from "@content-factory/content-schema-ts";
import { useMemo, type ReactElement } from "react";
import { AbsoluteFill, Sequence, useCurrentFrame, useVideoConfig } from "remotion";

import { bundleCapabilityErrors, type ExplainerCompositionProps } from "./capabilities";
import { SceneEnvContext, buildSceneEnv, useSceneEnv } from "./context";
import { tokenHex } from "./palette";
import { resolveSceneState } from "./state";
import { ChartTemplate } from "./templates/ChartTemplate";
import { DiagramTemplate } from "./templates/DiagramTemplate";
import { TextTemplate } from "./templates/TextTemplate";
import { TEXT_STACK } from "./text";

export { calculateExplainerMetadata, type ExplainerCompositionProps } from "./capabilities";

function CompiledSceneView({ compiled }: { compiled: CompiledExplainerScene }): ReactElement {
  const env = useSceneEnv();
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const scene = env.scenes.get(compiled.scene_id);
  if (!scene) throw new Error(`compiled scene ${compiled.scene_id} is not in the spec`);
  const layout = env.layouts.get(compiled.scene_id) ?? null;
  const state = resolveSceneState(compiled, scene, frame, fps, { layout, canvas: { width, height } });
  const template = scene.template;
  switch (template.template) {
    case "chart":
      return <ChartTemplate compiled={compiled} scene={scene} template={template} state={state} />;
    case "diagram":
      if (!layout) throw new Error(`scene ${compiled.scene_id} is a diagram without a DiagramLayout in bundle.layouts`);
      return <DiagramTemplate compiled={compiled} scene={scene} template={template} state={state} layout={layout} />;
    case "text":
      return <TextTemplate compiled={compiled} scene={scene} template={template} state={state} />;
    default:
      throw new Error(`scene ${compiled.scene_id} uses ${template.template}; the source_document renderer arrives in Phase 3`);
  }
}

export function ExplainerComposition({ bundle }: ExplainerCompositionProps): ReactElement {
  const env = useMemo(() => buildSceneEnv(bundle), [bundle]);
  const errors = bundleCapabilityErrors(bundle);
  if (errors.length > 0) throw new Error(errors.join("\n"));
  return (
    <SceneEnvContext.Provider value={env}>
      <AbsoluteFill style={{ background: tokenHex("ui.surface.0"), fontFamily: TEXT_STACK }}>
        {bundle.timeline.scenes.map((compiled) => (
          <Sequence key={compiled.scene_id} name={compiled.scene_id} from={compiled.start_frame} durationInFrames={compiled.duration_frames}>
            <CompiledSceneView compiled={compiled} />
          </Sequence>
        ))}
      </AbsoluteFill>
    </SceneEnvContext.Provider>
  );
}
