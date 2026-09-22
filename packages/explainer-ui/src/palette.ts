// Every colour a template draws: a design token by id, or the entity colour the compiler resolved.
import type { CompiledExplainerScene } from "@content-factory/content-schema-ts";

import { oklchToHex, oklchToRgba, type Oklch } from "./oklch";
import { TOKENS } from "./tokens.gen";

type UiToken = (typeof TOKENS.color.ui)[number];
type CategoricalToken = (typeof TOKENS.color.data.categorical)[number];
type StateToken = (typeof TOKENS.color.state)[number];
type ColouredStateToken = Extract<StateToken, { oklch: readonly [number, number, number] }>;

export type TokenId = UiToken["id"] | CategoricalToken["id"] | ColouredStateToken["id"];

const TOKEN_COLORS: ReadonlyArray<{ readonly id: string; readonly oklch: Oklch }> = [
  ...TOKENS.color.ui,
  ...TOKENS.color.data.categorical,
  ...TOKENS.color.state.filter((t): t is ColouredStateToken => "oklch" in t),
];

function oklchOf(id: TokenId): Oklch {
  const token = TOKEN_COLORS.find((t) => t.id === id);
  if (!token) throw new Error(`unknown colour token ${id}`);
  return token.oklch;
}

function stateAlpha(id: string): number {
  const token = TOKENS.color.state.find((t) => t.id === id);
  if (!token || !("alpha" in token)) throw new Error(`state token ${id} has no alpha`);
  return token.alpha;
}

/** Alpha the design system leaves on everything that is not the thing being talked about. */
export const DEEMPHASIS_ALPHA: number = stateAlpha("state.deemphasis");

const hexCache = new Map<string, string>();

/** sRGB hex of a design token, memoised because every frame asks for the same few. */
export function tokenHex(id: TokenId): string {
  let hex = hexCache.get(id);
  if (hex === undefined) {
    hex = oklchToHex(oklchOf(id));
    hexCache.set(id, hex);
  }
  return hex;
}

export function tokenRgba(id: TokenId, alpha: number): string {
  return oklchToRgba(oklchOf(id), alpha);
}

export function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace(/^#/, "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

export function rgbaOf(hex: string, alpha: number): string {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Channel-wise sRGB mix of two hexes at `t` in 0..1; a value change, so linear by design. */
export function mixHex(a: string, b: string, t: number): string {
  if (t <= 0) return a;
  if (t >= 1) return b;
  const ra = hexToRgb(a);
  const rb = hexToRgb(b);
  const mixed = ra.map((c, i) => Math.round(c + ((rb[i] ?? c) - c) * t));
  return `#${mixed.map((c) => c.toString(16).toUpperCase().padStart(2, "0")).join("")}`;
}

/** `entity_id` → `srgb_hex` as the compiler allocated them for this scene. */
export function entityColors(scene: CompiledExplainerScene): ReadonlyMap<string, string> {
  return new Map(scene.colors.map((c) => [c.entity_id, c.srgb_hex.toUpperCase()] as const));
}

/** The compiled colour of an entity, or a token when the compiler allocated none. */
export function entityHex(colors: ReadonlyMap<string, string>, entityId: string, fallback: TokenId): string {
  return colors.get(entityId) ?? tokenHex(fallback);
}

/** Categorical token `i` (wrapping), the allocator's own preference order. */
export function categoricalTokenId(i: number): TokenId {
  const palette = TOKENS.color.data.categorical;
  return palette[((i % palette.length) + palette.length) % palette.length]?.id ?? palette[0].id;
}
