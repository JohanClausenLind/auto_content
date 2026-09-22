// What this renderer can draw; kept free of runtime imports so a Node script can load it directly.
import type { ExplainerRenderBundle } from "@content-factory/content-schema-ts";
import type { CalculateMetadataFunction } from "remotion";

export interface ExplainerCompositionProps extends Record<string, unknown> {
  bundle: ExplainerRenderBundle;
}

export const SUPPORTED_TEMPLATES: ReadonlySet<string> = new Set(["chart", "diagram", "text"]);
const SOURCE_DOCUMENT_ACTIONS: ReadonlySet<string> = new Set(["show_source", "scroll_to", "focus_passage", "highlight_quote"]);
const LATER = "the source_document renderer arrives in Phase 3";

/** One line per scene this renderer cannot draw yet, or whose bundle data it cannot find. */
export function bundleCapabilityErrors(bundle: ExplainerRenderBundle): string[] {
  const errors: string[] = [];
  const compiled = new Set(bundle.timeline.scenes.map((s) => s.scene_id));
  const layouts = new Set(bundle.layouts.map((l) => l.scene_id));
  const datasets = new Set(bundle.datasets.map((d) => d.dataset_id));
  const assets = new Map(bundle.spec.assets.map((a) => [a.asset_id, a] as const));
  const specScenes = new Set<string>();
  for (const scene of bundle.spec.scenes) {
    specScenes.add(scene.scene_id);
    const template = scene.template;
    if (!SUPPORTED_TEMPLATES.has(template.template)) {
      errors.push(`scene ${scene.scene_id} uses ${template.template}; ${LATER}`);
    } else {
      for (const beat of scene.beats) {
        for (const action of beat.actions) {
          if (SOURCE_DOCUMENT_ACTIONS.has(action.action)) errors.push(`scene ${scene.scene_id} beat ${beat.beat_id} uses ${action.action}; ${LATER}`);
        }
      }
    }
    if (template.template === "diagram" && !layouts.has(scene.scene_id)) errors.push(`scene ${scene.scene_id} is a diagram without a DiagramLayout in bundle.layouts`);
    if (template.template === "chart") {
      const asset = assets.get(template.dataset_asset_id);
      if (!asset || asset.dataset_id === null || !datasets.has(asset.dataset_id)) {
        errors.push(`scene ${scene.scene_id} charts asset ${template.dataset_asset_id}, whose dataset is not in bundle.datasets`);
      }
    }
    if (!compiled.has(scene.scene_id)) errors.push(`scene ${scene.scene_id} has no compiled scene in the timeline`);
  }
  for (const scene of bundle.timeline.scenes) {
    if (!specScenes.has(scene.scene_id)) errors.push(`compiled scene ${scene.scene_id} is not in the spec`);
  }
  return errors;
}

/** Remotion metadata straight from the compiled timeline. */
export const calculateExplainerMetadata: CalculateMetadataFunction<ExplainerCompositionProps> = ({ props }) => {
  const timeline = props.bundle.timeline;
  return { fps: timeline.fps, width: timeline.width, height: timeline.height, durationInFrames: timeline.total_frames };
};
