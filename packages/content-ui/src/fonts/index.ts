// Pinned local font: Inter via @fontsource/inter (SIL OFL 1.1). Never loaded from the network.
import { INTER_WEIGHTS, type InterWeight } from "./inter-widths";

export { INTER_VERSION, INTER_WEIGHTS, INTER_WIDTHS, type InterWeight } from "./inter-widths";

export const INTER_FAMILY = "Inter";
/** Fallbacks exist only for non-Remotion previews; renders wait for Inter before drawing. */
export const FONT_STACK = `${INTER_FAMILY}, "Helvetica Neue", Arial, sans-serif`;

export interface InterFace {
  weight: InterWeight;
  style: "normal";
  /** File name inside `@fontsource/inter/files/`. */
  file: string;
}

/** The faces the content design system uses. Renderers copy exactly these files into `public/fonts/`. */
export const INTER_FACES: readonly InterFace[] = INTER_WEIGHTS.map((weight) => ({
  weight,
  style: "normal",
  file: `inter-latin-${weight}-normal.woff2`,
}));

/**
 * `@font-face` CSS for the pinned faces. `urlFor` maps a file name to a URL the renderer can
 * serve (e.g. Remotion's `staticFile("fonts/" + file)`), so the CSS never references a CDN.
 */
export function interFontFaceCss(urlFor: (file: string) => string): string {
  return INTER_FACES.map(
    (face) =>
      `@font-face{font-family:"${INTER_FAMILY}";font-style:${face.style};font-weight:${face.weight};` +
      `font-display:block;src:url("${urlFor(face.file)}") format("woff2");}`,
  ).join("\n");
}
