// Page tiles under a camera that scrolls and zooms, quote overlays glued to the page, the source named below.
import type { SourceDocumentTemplate as SourceSpec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { Img, useCurrentFrame } from "remotion";

import { useSceneEnv } from "../context";
import { clamp01, regionOf, safeAreaBox } from "../geometry";
import { tokenHex } from "../palette";
import { cameraAt, highlightOpacity, lineProgress, pageScale, pageTransform, tileUrl } from "../source";
import { eased } from "../state";
import { TEXT_STACK, lineHeightPx, rolePx } from "../text";
import { TOKENS } from "../tokens.gen";
import type { TemplateProps } from "./props";

export function SourceDocumentTemplate({ compiled, template }: TemplateProps<SourceSpec>): ReactElement {
  const env = useSceneEnv();
  // Camera and highlight keys are timeline-absolute, like action frames.
  const frame = useCurrentFrame() + compiled.start_frame;
  const { fps, width, height } = env.bundle.timeline;
  const asset = env.assets.get(template.capture_asset_id);
  const capture = asset?.capture_id == null ? undefined : env.captures.get(asset.capture_id);
  if (!capture) throw new Error(`scene ${compiled.scene_id} shows capture ${asset?.capture_id ?? template.capture_asset_id}, which is not in bundle.captures`);
  const manifest = capture.manifest;
  const region = regionOf(compiled, "page", "content") ?? safeAreaBox(width, height);
  const camera = cameraAt(compiled.camera, frame);
  const scale = pageScale(region, manifest.viewport.width, camera.zoom);
  const show = compiled.actions.find((a) => a.action === "show_source");
  const fadeFrames = Math.max(1, Math.round((TOKENS.motion.duration_ms.reveal / 1000) * fps));
  const shown = show ? eased("show_source", clamp01((frame - show.start_frame) / fadeFrames)) : 1;
  const unit = TOKENS.layout.grid.unit * env.scale;
  const labelPx = rolePx("label", env.scale);
  const captured = manifest.captured_at.slice(0, 10);
  const attribution = [manifest.publisher, manifest.title, `captured ${captured}`].filter((part) => part !== "").join(" · ");
  return (
    <>
      <div style={{ position: "absolute", left: region.x, top: region.y, width: region.width, height: region.height, overflow: "hidden", opacity: shown, background: tokenHex("ui.surface.1") }}>
        <div style={{ position: "absolute", left: 0, top: 0, width: manifest.viewport.width, height: manifest.page_height_px, transformOrigin: "0 0", transform: pageTransform(camera, scale) }}>
          {capture.tiles.map((tile, i) => (
            <Img key={`${i}-${tile.sha256}`} src={tileUrl(tile.path)} style={{ position: "absolute", left: 0, top: tile.y_px, width: tile.width, height: tile.height, display: "block" }} />
          ))}
          {compiled.highlights.map((key) => {
            const opacity = highlightOpacity(key, frame);
            if (opacity <= 0) return null;
            return key.rects.map((rect, i) => {
              const progress = lineProgress(key, i, frame);
              if (progress <= 0) return null;
              // Multiply is the raster form of "behind the text": the paper takes the amber, the ink keeps its depth.
              return <div key={`${key.quote_id}-${i}`} style={{ position: "absolute", left: rect.x, top: rect.y, width: rect.width * progress, height: rect.height, background: key.rgba, mixBlendMode: "multiply", opacity }} />;
            });
          })}
        </div>
      </div>
      <div style={{ position: "absolute", left: region.x, top: region.y + region.height + unit, width: region.width, height: lineHeightPx(labelPx), lineHeight: `${lineHeightPx(labelPx)}px`, fontFamily: TEXT_STACK, fontSize: labelPx, fontWeight: 500, color: tokenHex("ui.ink.secondary"), whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", opacity: shown }}>
        {attribution}
      </div>
    </>
  );
}
