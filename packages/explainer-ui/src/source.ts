// Source-document camera and highlight math: pure functions of the compiled keys and the frame.
import type { CameraKey, HighlightKey, PageRect, PixelBox } from "@content-factory/content-schema-ts";
import { Easing, staticFile } from "remotion";

import { clamp01, lerp } from "./geometry";

export { isLocalPath } from "./capabilities";
import { TOKENS } from "./tokens.gen";

/** scroll is the page pixel at the region's top-left; zoom 1 fits the page width to the region. */
export interface SourceCamera {
  scrollX: number;
  scrollY: number;
  zoom: number;
}

export interface ScreenRect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

const EASE: Record<CameraKey["easing"], (t: number) => number> = {
  move: Easing.bezier(...TOKENS.motion.easing.move),
  standard: Easing.bezier(...TOKENS.motion.easing.standard),
  hold: () => 0,
};

function stateOf(key: CameraKey): SourceCamera {
  return { scrollX: key.scroll_x, scrollY: key.scroll_y, zoom: key.zoom };
}

/** The camera at a timeline frame: held before the first key, eased by the later key between two. */
export function cameraAt(keys: readonly CameraKey[], frame: number): SourceCamera {
  const first = keys[0];
  if (!first) return { scrollX: 0, scrollY: 0, zoom: 1 };
  if (frame <= first.frame) return stateOf(first);
  for (let i = 0; i + 1 < keys.length; i += 1) {
    const a = keys[i];
    const b = keys[i + 1];
    if (!a || !b || frame >= b.frame) continue;
    const t = EASE[b.easing]((frame - a.frame) / (b.frame - a.frame));
    return { scrollX: lerp(a.scroll_x, b.scroll_x, t), scrollY: lerp(a.scroll_y, b.scroll_y, t), zoom: lerp(a.zoom, b.zoom, t) };
  }
  const last = keys[keys.length - 1];
  return last ? stateOf(last) : stateOf(first);
}

/** Canvas px per page px at this zoom. */
export function pageScale(region: PixelBox, pageWidth: number, zoom: number): number {
  return (region.width / pageWidth) * zoom;
}

function round3(v: number): number {
  return Math.round(v * 1000) / 1000;
}

/** CSS transform for the page group with its origin at the region's top-left: scroll in page px, then scale. */
export function pageTransform(camera: SourceCamera, scale: number): string {
  return `scale(${round3(scale)}) translate(${round3(-camera.scrollX)}px, ${round3(-camera.scrollY)}px)`;
}

/** A page rect on the canvas under this camera. */
export function screenRect(rect: PageRect, camera: SourceCamera, region: PixelBox, scale: number): ScreenRect {
  const x0 = region.x + (rect.x - camera.scrollX) * scale;
  const y0 = region.y + (rect.y - camera.scrollY) * scale;
  return { x0, y0, x1: x0 + rect.width * scale, y1: y0 + rect.height * scale };
}

/** How much of line `line` the sweep has drawn at `frame`, 0..1, left to right. */
export function lineProgress(key: HighlightKey, line: number, frame: number): number {
  const perLine = (key.end_frame - key.start_frame) / key.rects.length;
  if (perLine <= 0) return frame >= key.start_frame ? 1 : 0;
  return clamp01((frame - key.start_frame - line * perLine) / perLine);
}

/** 1 while the highlight stands; the clear fades it to 0 between its two frames. */
export function highlightOpacity(key: HighlightKey, frame: number): number {
  if (key.clear_start_frame === null || key.clear_end_frame === null) return 1;
  const span = Math.max(1, key.clear_end_frame - key.clear_start_frame);
  return 1 - clamp01((frame - key.clear_start_frame) / span);
}

/** A tile path to a URL exactly as the timeline renderer maps its assets (ArtboardComposition.assetUrl). */
export function tileUrl(path: string): string {
  return /^(https?:|data:|blob:|file:)/.test(path) ? path : staticFile(path.replace(/^\/+/, ""));
}
