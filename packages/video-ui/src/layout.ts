import type { AspectRatio, ContentTheme } from "@content-factory/content-ui";

export interface PxBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

const ASPECTS: ReadonlyArray<[AspectRatio, number]> = [
  ["16:9", 16 / 9],
  ["9:16", 9 / 16],
  ["1:1", 1],
  ["4:5", 4 / 5],
];

/** Closest supported aspect for a composition size. */
export function aspectFor(width: number, height: number): AspectRatio {
  const r = width / height;
  let best = ASPECTS[0]!;
  for (const a of ASPECTS) if (Math.abs(Math.log(r / a[1])) < Math.abs(Math.log(r / best[1]))) best = a;
  return best[0];
}

/** Safe area in px for the composition, from the theme grid of the closest aspect. */
export function safeBox(theme: ContentTheme, width: number, height: number): PxBox {
  const safe = theme.grid[aspectFor(width, height)].safe;
  const left = Math.round(safe.x * width);
  const top = Math.round(safe.y * height);
  return { left, top, width: Math.round((safe.x + safe.w) * width) - left, height: Math.round((safe.y + safe.h) * height) - top };
}
