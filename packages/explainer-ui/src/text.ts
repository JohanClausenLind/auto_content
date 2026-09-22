// Typography roles: the pinned faces and the 1080-referenced sizes; text is never shrunk to fit.
import { TOKENS } from "./tokens.gen";

export const TEXT_STACK = '"Inter", sans-serif';
export const DISPLAY_STACK = '"Sora", "Inter", sans-serif';
export const LINE_HEIGHT: number = TOKENS.typography.line_height;

export type TypeRole = keyof typeof TOKENS.typography.scale_px_at_1080;

/** Role size in px on a canvas `scale` times the 1080 reference. */
export function rolePx(role: TypeRole, scale: number): number {
  return Math.round(TOKENS.typography.scale_px_at_1080[role] * scale);
}

export function lineHeightPx(fontPx: number): number {
  return Math.round(fontPx * LINE_HEIGHT);
}
