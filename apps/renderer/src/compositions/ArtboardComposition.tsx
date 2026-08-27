import type { RenderBundle } from "@content-factory/content-schema-ts";
import { Artboard, type AssetUrlResolver } from "@content-factory/content-ui";
import { AbsoluteFill, staticFile, type CalculateMetadataFunction } from "remotion";

import "../fonts";

export interface BundleProps extends Record<string, unknown> {
  bundle: RenderBundle;
}

/** `bundle.assets` values are local paths relative to public/ unless they are already URLs. */
export const assetUrl: AssetUrlResolver = (_assetId, path) =>
  /^(https?:|data:|blob:|file:)/.test(path) ? path : staticFile(path.replace(/^\/+/, ""));

export const ArtboardComposition: React.FC<BundleProps> = ({ bundle }) => (
  <AbsoluteFill>
    <Artboard bundle={bundle} assetUrl={assetUrl} />
  </AbsoluteFill>
);

export const calculateArtboardMetadata: CalculateMetadataFunction<BundleProps> = ({ props }) => {
  const artboard = props.bundle.artboard;
  if (!artboard) throw new Error(`RenderBundle ${props.bundle.bundle_id} has no artboard`);
  return { width: artboard.width, height: artboard.height };
};
