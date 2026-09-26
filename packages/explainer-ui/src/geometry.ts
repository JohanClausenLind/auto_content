// Regions, entity boxes and the camera: pixel geometry the compiler fixed before any frame renders.
import type { CompiledExplainerScene, DiagramLayout, EntityBox, NamedRegion, PixelBox } from "@content-factory/content-schema-ts";

import { lineHeightPx } from "./text";
import { TOKENS } from "./tokens.gen";

export type { PixelBox };
export type RegionName = NamedRegion["name"];

/** The first named region present, in the order given, or null when the scene has none of them. */
export function regionOf(scene: CompiledExplainerScene, ...names: RegionName[]): PixelBox | null {
  for (const name of names) {
    const hit = scene.regions.find((r) => r.name === name);
    if (hit) return hit.box;
  }
  return null;
}

export function scaleFor(height: number): number {
  return height / TOKENS.layout.canvas.height;
}

/** Canvas minus the safe area, with the 1080-based insets scaled to this canvas height. */
export function safeAreaBox(width: number, height: number): PixelBox {
  const s = scaleFor(height);
  const x = Math.round(TOKENS.layout.safe_area_px.x * s);
  const y = Math.round(TOKENS.layout.safe_area_px.y * s);
  return { x, y, width: width - 2 * x, height: height - 2 * y };
}

export function boxCenter(box: PixelBox): { x: number; y: number } {
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** Node boxes from the layout first, then the compiler's boxes, which win on conflict. */
export function entityBoxes(scene: CompiledExplainerScene, layout: DiagramLayout | null = null): ReadonlyMap<string, PixelBox> {
  const out = new Map<string, PixelBox>();
  for (const node of layout?.nodes ?? []) out.set(node.entity_id, node.box);
  for (const box of scene.boxes) out.set(box.entity_id, box.box);
  return out;
}

export function entityBoxIndex(scene: CompiledExplainerScene): ReadonlyMap<string, EntityBox> {
  return new Map(scene.boxes.map((b) => [b.entity_id, b] as const));
}

/** The line block centred in a compiler box, which carries layout.py's TEXT_PAD (half a unit) around it. */
export function innerBlock(box: EntityBox, fontPx: number, unit: number): PixelBox {
  const b = box.box;
  const pad = unit / 2;
  const height = Math.min(b.height, box.lines * lineHeightPx(fontPx));
  return { x: b.x + pad, y: b.y + (b.height - height) / 2, width: b.width - 2 * pad, height };
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function clamp01(v: number): number {
  return v <= 0 ? 0 : v >= 1 ? 1 : v;
}

export interface Camera {
  scale: number;
  tx: number;
  ty: number;
}

export const IDENTITY_CAMERA: Camera = { scale: 1, tx: 0, ty: 0 };

/** 2.5: the design system's ceiling on a zoom factor (docs/DESIGN_SYSTEM.md, motion.rules). */
export const MAX_ZOOM = 2.5;

function centred(target: PixelBox, region: PixelBox, scale: number): Camera {
  const c = boxCenter(target);
  const r = boxCenter(region);
  return { scale, tx: r.x - scale * c.x, ty: r.y - scale * c.y };
}

/** Fit `target` plus padding into `region`, never below 1 and never above MAX_ZOOM, centred on it. */
export function zoomCamera(target: PixelBox, region: PixelBox, padPx: number): Camera {
  const fit = Math.min(region.width / (target.width + 2 * padPx), region.height / (target.height + 2 * padPx));
  return centred(target, region, Math.min(MAX_ZOOM, Math.max(1, fit)));
}

/** Keep the current scale and centre the region on `target`. */
export function panCamera(target: PixelBox, region: PixelBox, scale: number): Camera {
  return centred(target, region, scale);
}

export function lerpCamera(a: Camera, b: Camera, t: number): Camera {
  return { scale: lerp(a.scale, b.scale, t), tx: lerp(a.tx, b.tx, t), ty: lerp(a.ty, b.ty, t) };
}

function round3(v: number): number {
  return Math.round(v * 1000) / 1000;
}

/** SVG transform for a camera: translate first, then scale about the origin. */
export function cameraTransform(c: Camera): string {
  return `translate(${round3(c.tx)} ${round3(c.ty)}) scale(${round3(c.scale)})`;
}
