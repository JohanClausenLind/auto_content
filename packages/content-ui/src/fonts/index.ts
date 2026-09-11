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
/**
 * The house face first, the pinned faces behind it.
 *
 * `HelveticaNeue Condensed` is the operator's chosen face (2026-09-12) and is installed on the
 * host, not vendored: it is licensed, and `docs/licensing.md` bars redistributing licensed media —
 * a repo is redistribution. So it is named here and resolved by the system, exactly as the burn-in
 * captions resolve it through fontconfig.
 *
 * Inter and Sora stay pinned and stay in the stack, and that is the guarantee rather than the
 * fallback: a machine without Helvetica renders in Inter instead of rendering in nothing, and
 * `loadFont` still waits on the pinned faces before drawing. A stack whose first entry is absent
 * costs nothing at render time.
 */
const HOUSE_FAMILY = `"HelveticaNeue Condensed"`;
export const FONT_STACK = `${HOUSE_FAMILY}, ${INTER_FAMILY}, "Helvetica Neue", Arial, sans-serif`;
export const DISPLAY_FONT_STACK = `${HOUSE_FAMILY}, ${SORA_FAMILY}, ${INTER_FAMILY}, "Helvetica Neue", Arial, sans-serif`;

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
