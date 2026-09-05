import type { ScreenshotScene as Spec } from "@content-factory/content-schema-ts";
import { useEffect, useState, type ReactElement } from "react";
import { continueRender, delayRender, Img, useCurrentFrame } from "remotion";

import { useSceneEnv } from "../context";
import { enter, motionFrames, progress } from "../motion";
import { PlaceholderCard } from "./Placeholder";
import { hostOf, SceneFrame, useSceneGeometry } from "./common";

export interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** Where an image of `natural` size lands inside `box` under object-fit: contain. Letterboxing is
 * split evenly, so a highlight expressed in image fractions can be placed on the rendered pixels
 * rather than on the container. */
export function containRect(natural: { width: number; height: number }, box: { width: number; height: number }): Rect {
  if (natural.width <= 0 || natural.height <= 0) return { left: 0, top: 0, ...box };
  const fit = Math.min(box.width / natural.width, box.height / natural.height);
  const width = natural.width * fit;
  const height = natural.height * fit;
  return { left: (box.width - width) / 2, top: (box.height - height) / 2, width, height };
}

/** The contract's highlight_region is four floats as fractions of the image: x, y, w, h. Values
 * are clamped into the image, and anything non-numeric drops the highlight entirely rather than
 * drawing a box in the wrong place. */
export function highlightBox(region: readonly unknown[] | null, image: Rect): Rect | null {
  if (region === null || region.length !== 4) return null;
  const nums = region.map((v) => (typeof v === "number" && Number.isFinite(v) ? v : Number.NaN));
  if (nums.some(Number.isNaN)) return null;
  const [x, y, w, h] = nums as [number, number, number, number];
  const clamp = (v: number): number => Math.min(1, Math.max(0, v));
  const left = clamp(x);
  const top = clamp(y);
  return {
    left: image.left + left * image.width,
    top: image.top + top * image.height,
    width: Math.min(1 - left, Math.max(0, w)) * image.width,
    height: Math.min(1 - top, Math.max(0, h)) * image.height,
  };
}

/**
 * A captured web page with an optional highlight box. Playwright produces the PNG in a capture
 * step before the render (`apps/renderer/scripts/capture-screenshot.mjs`); by the time this scene
 * runs the image is just another bundle asset. The render is held until the bitmap has decoded so
 * its natural size is known, which keeps the highlight aligned on every frame.
 */
export function ScreenshotScene({ scene, compiled }: { scene: Spec; compiled: { scene_id: string } }): ReactElement {
  const frame = useCurrentFrame();
  const { bundle, theme, assetUrl } = useSceneEnv();
  const { safe, scale, fps, width, height } = useSceneGeometry();
  const f = motionFrames(theme, fps);

  const path = bundle.assets[scene.asset_id];
  const [handle] = useState(() => delayRender(`screenshot:${compiled.scene_id}`));
  const [natural, setNatural] = useState<{ width: number; height: number } | null>(null);

  // Releasing the hold is a side effect, so it runs in an effect rather than the render pass.
  useEffect(() => {
    if (path === undefined) continueRender(handle);
  }, [handle, path]);

  if (path === undefined) {
    return (
      <PlaceholderCard
        kind="screenshot"
        title={`missing asset · ${scene.asset_id}`}
        sceneId={compiled.scene_id}
        width={width}
        height={height}
        theme={theme}
      />
    );
  }

  const box = { width: safe.width, height: Math.round(safe.height * 0.78) };
  const image = containRect(natural ?? box, box);
  const highlight = natural === null ? null : highlightBox(scene.highlight_region, image);
  const reveal = progress(frame, Math.round(f.fast / 2), f.base, theme.motion.easing.decelerate);
  const source = bundle.sources?.[scene.source_id];

  return (
    <SceneFrame testId="screenshot" justify="start">
      <div style={{ position: "relative", ...box, ...enter(frame, 0, f.base, theme, 12 * scale) }}>
        <Img
          src={assetUrl(scene.asset_id, path)}
          alt={scene.alt_text}
          onLoad={(e) => {
            const img = e.currentTarget;
            setNatural({ width: img.naturalWidth, height: img.naturalHeight });
            continueRender(handle);
          }}
          onError={() => {
            continueRender(handle);
          }}
          style={{ width: box.width, height: box.height, objectFit: "contain" }}
        />
        {highlight !== null && (
          <div
            data-highlight="true"
            style={{
              position: "absolute",
              left: highlight.left,
              top: highlight.top,
              width: highlight.width,
              height: highlight.height,
              border: `${Math.max(2, Math.round(3 * scale))}px solid ${theme.color.accent}`,
              borderRadius: theme.radius.sm * scale,
              // Frame-driven, so the box is drawn identically on every re-render of this frame.
              opacity: reveal,
            }}
          />
        )}
      </div>
      <div style={{ marginTop: 8 * scale, color: theme.color.muted, fontSize: 20 * scale }}>
        {source ? `${source.publisher || hostOf(source.url)} · ${source.accessed}` : scene.alt_text}
      </div>
    </SceneFrame>
  );
}
