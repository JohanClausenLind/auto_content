// Pinned local fonts via @fontsource (SIL OFL 1.1). Never loaded from the network.
//   Inter — the body face everywhere (labels, body, captions, sources, sub-heads).
//   Sora  — an optional display face (display, headline, number) a brand can opt into.
import { INTER_WEIGHTS, type InterWeight } from "./inter-widths";
import { SORA_WEIGHTS, type SoraWeight } from "./sora-widths";

export { INTER_VERSION, INTER_WEIGHTS, INTER_WIDTHS, type InterWeight } from "./inter-widths";
export { SORA_VERSION, SORA_WEIGHTS, SORA_WIDTHS, type SoraWeight } from "./sora-widths";

export type FontFamily = "Inter" | "Sora";
export type FontWeight = InterWeight | SoraWeight;

export const INTER_FAMILY = "Inter";
export const SORA_FAMILY = "Sora";
/** Fallbacks exist only for non-Remotion previews; renders wait for the faces before drawing. */
export const FONT_STACK = `${INTER_FAMILY}, "Helvetica Neue", Arial, sans-serif`;
export const DISPLAY_FONT_STACK = `${SORA_FAMILY}, ${INTER_FAMILY}, "Helvetica Neue", Arial, sans-serif`;

/** CSS font stack for a pinned family. */
export function fontStackFor(family: FontFamily): string {
  return family === "Sora" ? DISPLAY_FONT_STACK : FONT_STACK;
}

export interface FontFace {
  family: FontFamily;
  /** The @fontsource package that ships the file. */
  pkg: string;
  weight: FontWeight;
  style: "normal";
  /** File name inside `<pkg>/files/`. */
  file: string;
}
/** Kept for callers that predate the second family. */
export type InterFace = FontFace;

/** The faces the content design system uses. Renderers copy exactly these files into `public/fonts/`. */
export const INTER_FACES: readonly FontFace[] = INTER_WEIGHTS.map((weight) => ({
  family: "Inter" as const,
  pkg: "@fontsource/inter",
  weight,
  style: "normal" as const,
  file: `inter-latin-${weight}-normal.woff2`,
}));
export const SORA_FACES: readonly FontFace[] = SORA_WEIGHTS.map((weight) => ({
  family: "Sora" as const,
  pkg: "@fontsource/sora",
  weight,
  style: "normal" as const,
  file: `sora-latin-${weight}-normal.woff2`,
}));
export const FONT_FACES: readonly FontFace[] = [...INTER_FACES, ...SORA_FACES];

/**
 * `@font-face` CSS for the pinned faces. `urlFor` maps a file name to a URL the renderer can
 * serve (e.g. Remotion's `staticFile("fonts/" + file)`), so the CSS never references a CDN.
 */
export function fontFaceCss(urlFor: (file: string) => string, faces: readonly FontFace[] = FONT_FACES): string {
  return faces
    .map(
      (face) =>
        `@font-face{font-family:"${face.family}";font-style:${face.style};font-weight:${face.weight};` +
        `font-display:block;src:url("${urlFor(face.file)}") format("woff2");}`,
    )
    .join("\n");
}
export function interFontFaceCss(urlFor: (file: string) => string): string {
  return fontFaceCss(urlFor, INTER_FACES);
}
