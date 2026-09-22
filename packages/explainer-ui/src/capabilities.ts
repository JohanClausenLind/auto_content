// What this renderer can draw; kept free of runtime imports so a Node script can load it directly.
import type { ExplainerRenderBundle } from "@content-factory/content-schema-ts";
import type { CalculateMetadataFunction } from "remotion";

export interface ExplainerCompositionProps extends Record<string, unknown> {
  bundle: ExplainerRenderBundle;
}

export const SUPPORTED_TEMPLATES: ReadonlySet<string> = new Set(["chart", "diagram", "text", "source_document"]);

/** An absolute local path the browser cannot reach; the bundle needs staging first (render.stage_captures). */
export function isLocalPath(path: string): boolean {
  return /^(\/|[A-Za-z]:[\\/])/.test(path);
}

/** One line per scene this renderer cannot draw yet, or whose bundle data it cannot find. */
export function bundleCapabilityErrors(bundle: ExplainerRenderBundle): string[] {
  const errors: string[] = [];
  const compiled = new Set(bundle.timeline.scenes.map((s) => s.scene_id));
  const layouts = new Set(bundle.layouts.map((l) => l.scene_id));
  const datasets = new Set(bundle.datasets.map((d) => d.dataset_id));
  const assets = new Map(bundle.spec.assets.map((a) => [a.asset_id, a] as const));
  const captures = new Map(bundle.captures.map((c) => [c.capture_id, c] as const));
  const specScenes = new Set<string>();
  for (const scene of bundle.spec.scenes) {
    specScenes.add(scene.scene_id);
    const template = scene.template;
    if (!SUPPORTED_TEMPLATES.has(template.template)) errors.push(`scene ${scene.scene_id} uses ${template.template}, which this renderer cannot draw`);
    if (template.template === "source_document") {
      const asset = assets.get(template.capture_asset_id);
      const capture = asset?.capture_id == null ? undefined : captures.get(asset.capture_id);
      if (!capture) errors.push(`scene ${scene.scene_id} shows capture ${asset?.capture_id ?? template.capture_asset_id}, which is not in bundle.captures`);
      const local = capture?.tiles.find((t) => isLocalPath(t.path));
      if (local) errors.push(`capture ${capture?.capture_id} tile ${local.path} is a local path; stage the bundle first (content_factory.explainer.render.stage_captures)`);
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
