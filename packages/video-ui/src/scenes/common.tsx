import type { CompiledScene, SceneSpec, SourceCard } from "@content-factory/content-schema-ts";
import {
  FONT_STACK,
  classificationNotice,
  fitText,
  fontStackFor,
  scaleFor,
  textColor,
  type BackgroundRole,
  type ContentTheme,
  type DataClassification,
  type FitResult,
  type TextRole,
} from "@content-factory/content-ui";
import type { CSSProperties, ReactElement, ReactNode } from "react";
import { useEffect, useState } from "react";
import { AbsoluteFill, continueRender, delayRender, Img, useCurrentFrame, useVideoConfig } from "remotion";

import { useSceneEnv } from "../context";
import { safeBox, type PxBox } from "../layout";

export interface SceneProps<S extends SceneSpec = SceneSpec> {
  scene: S;
  compiled: CompiledScene;
}

/** Fraction of the frame height above which portrait card content must stay (caption band below). */
export const CAPTION_BAND_TOP = 0.64;

export interface SceneGeometry {
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
  scale: number;
  safe: PxBox;
  theme: ContentTheme;
  /** Taller than wide (shorts): content centres, spines run vertically, type steps up a role. */
  portrait: boolean;
  /** Text alignment that matches the composition: centred in portrait, left otherwise. */
  align: "left" | "center";
}

export function useSceneGeometry(): SceneGeometry {
  const { width, height, fps, durationInFrames } = useVideoConfig();
  const { theme } = useSceneEnv();
  const portrait = height > width;
  // Portrait shorts carry burned-in captions in the lower third (compose_video puts the block at
  // 22 % from the bottom), so the cards keep their content above that band: the safe area ends at
  // 64 % of the frame height and the content block centres inside what remains.
  const grid = safeBox(theme, width, height);
  const safe = portrait ? { ...grid, height: Math.min(grid.height, Math.round(height * CAPTION_BAND_TOP) - grid.top) } : grid;
  return {
    width,
    height,
    fps,
    durationInFrames,
    scale: scaleFor(width, height),
    safe,
    theme,
    portrait,
    align: portrait ? "center" : "left",
  };
}

export interface SceneFrameProps {
  background?: BackgroundRole;
  /** Vertical placement of the content block inside the safe area. */
  justify?: "start" | "center" | "end";
  children: ReactNode;
  testId?: string;
  /** A still from `bundle.assets` to sit behind the type, dimmed. Ignored when absent from the
   * bundle, so a plan that names an asset `ingest` has not produced still renders as a card. */
  backgroundAssetId?: string | null | undefined;
  /** Classification of the data this scene shows, when it shows any. Anything that is not a
   * measurement gets a caveat pinned under the safe area for the scene's whole duration. */
  notice?: DataClassification | undefined;
  /** Overrides for the backdrop; `ImageScene` sets `scrim: 0` because the picture is the scene. */
  backdrop?: Omit<SceneBackdropProps, "assetId" | "background"> | undefined;
  /**
   * How far the scene's own type reaches across the safe area, in px — `fitText` measures it, so
   * the scene knows before it renders and the value is the same on every machine.
   *
   * It exists to aim the backdrop veil (see :data:`BACKDROP_SCRIM`). Landscape only: portrait
   * centres its content, where the band that matters is vertical and the anchor already covers
   * it. A scene that does not measure gets a veil flat across the safe area, which is what every
   * scene got before — a card must not lose its contrast floor by forgetting to opt in.
   */
  typeWidth?: number | undefined;
}

/** Background + safe-area container. Hard cuts: no enter/exit at the frame level. In portrait a
 * thin accent progress rule runs along the top of the safe area for the scene's duration. */
export function SceneFrame({ background = "paper", justify = "center", children, testId, backgroundAssetId = null, notice, backdrop, typeWidth }: SceneFrameProps): ReactElement {
  const { theme, safe, portrait, scale, durationInFrames, width } = useSceneGeometry();
  const frame = useCurrentFrame();
  const justifyContent = { start: "flex-start", center: "center", end: "flex-end" }[justify];
  const progressWidth = Math.round(safe.width * Math.min(1, frame / Math.max(1, durationInFrames - 1)));
  // Where the content block sits, which is where the veil has to stay full strength. A notice is
  // pinned outside the block and below the safe area, so a card carrying one keeps the flat veil.
  const anchor: ScrimAnchor = notice !== undefined ? "full" : portrait ? ({ start: "top", center: "middle", end: "bottom" } as const)[justify] : "left";
  const typeEnd = safe.left + Math.min(safe.width, typeWidth ?? safe.width);
  const plateau = anchor === "left" && typeWidth !== undefined ? (100 * typeEnd) / Math.max(1, width) : undefined;
  return (
    <AbsoluteFill data-scene={testId} style={{ background: theme.color[background], fontFamily: FONT_STACK, color: textColor(theme, "body", background) }}>
      <SceneBackdrop assetId={backgroundAssetId} background={background} anchor={anchor} plateau={plateau} {...backdrop} />
      {portrait ? (
        <div style={{ position: "absolute", left: safe.left, top: Math.max(0, safe.top - Math.round(28 * scale)), width: safe.width, height: Math.max(2, Math.round(4 * scale)), background: theme.color.rule }}>
          <div data-progress style={{ width: progressWidth, height: "100%", background: theme.color.accent }} />
        </div>
      ) : null}
      <div
        style={{
          position: "absolute",
          left: safe.left,
          top: safe.top,
          width: safe.width,
          height: safe.height,
          display: "flex",
          flexDirection: "column",
          justifyContent,
          alignItems: portrait ? "center" : "stretch",
          // Portrait cards drift very slowly larger over their duration (3 % end to end, linear,
          // no overshoot): a still card reads as frozen on a phone; a breathing one does not.
          transform: portrait ? `scale(${(1 + 0.03 * Math.min(1, frame / Math.max(1, durationInFrames - 1))).toFixed(4)})` : undefined,
          transformOrigin: "50% 45%",
        }}
      >
        {children}
      </div>
      <DataNotice classification={notice} background={background} />
    </AbsoluteFill>
  );
}

export interface FitBlock {
  fit: FitResult;
  style: CSSProperties;
}

/**
 * The smallest size the fitter may choose for a role before it truncates instead.
 *
 * This used to be `t.min * scale * 0.5` — half the role's own declared minimum, with nothing
 * saying so. Measured on the real portrait geometry (1080x1920, safe box 928 x 999 after the
 * caption band, six lines, `maxHeight = 0.4 x safe.height`): body text of 135 characters sets at
 * 30px, 407 characters at 28px, and **815 characters at 14px** — with `truncated: false`, because
 * the fitter shrank rather than cut. The theme declares `legibility.minFontPx1080 = 24` and
 * `legibilityReport` enforces it with a `font_too_small` finding, but that report runs on
 * `ArtboardSpec` only. Nothing on the video path checks a fitted size at all, so 14px shipped
 * looking exactly like 30px to every gate there is.
 *
 * So the floor is the theme's own declared floor, and text that will not fit above it is cut with
 * an ellipsis instead. That is louder, and being loud is the point: `legibilityReport` calls
 * truncation a blocker and a small font merely major, because an ellipsis in a delivered film is
 * something a person notices and 14px body text is not. The `Math.min` keeps the floor from ever
 * rising above the role's preferred size, so a role smaller than the floor still renders.
 */
export function fitFloorPx(theme: ContentTheme, role: TextRole | "display", scale: number): number {
  const t = theme.type[role];
  return Math.max(8, Math.min(t.size * scale, theme.legibility.minFontPx1080 * scale));
}

/**
 * The smallest size *any* text in a scene may be set at, in px for this frame.
 *
 * The companion to `fitFloorPx`, for the text that never goes through the fitter at all: axis
 * ticks, credit lines, timeline event labels, screenshot and map captions. Six of those were
 * hardcoded at 20 or 22 px against the theme's declared `legibility.minFontPx1080` of 24 —
 * measured on a landscape render of a card deck 2026-09-10, where the timeline's event text came
 * out at 22px on a 1920-wide frame and read as a grey smudge. Wrap a literal in
 * `Math.max(minTextPx(theme, scale), …)` rather than replacing it, so a size that is already
 * above the floor keeps whatever the design chose for it.
 */
export function minTextPx(theme: ContentTheme, scale: number): number {
  return Math.round(theme.legibility.minFontPx1080 * scale);
}

/** Deterministic fitted text block for a role within a box (px). */
export function useFittedText(text: string, role: TextRole | "display", maxWidth: number, maxHeight: number, maxLines: number, background: BackgroundRole, colorOverride?: string): FitBlock {
  const { theme, scale } = useSceneGeometry();
  const t = theme.type[role];
  const display = t.uppercase ? text.toUpperCase() : text;
  const fit = fitText({
    text: display,
    weight: t.weight,
    family: t.family,
    maxWidth,
    maxHeight,
    maxLines,
    preferredSize: t.size * scale,
    minSize: fitFloorPx(theme, role, scale),
    lineHeight: t.lineHeight,
    letterSpacing: t.letterSpacing,
  });
  return {
    fit,
    style: {
      fontFamily: fontStackFor(t.family),
      fontSize: fit.fontSize,
      fontWeight: t.weight,
      lineHeight: `${fit.lineHeightPx}px`,
      letterSpacing: `${t.letterSpacing}em`,
      color: colorOverride ?? textColor(theme, role === "display" ? "headline" : role, background),
      whiteSpace: "pre",
      width: maxWidth,
      height: fit.heightPx,
      overflow: "hidden",
      fontFeatureSettings: t.tabular ? '"tnum"' : undefined,
    },
  };
}

export function Lines({ block, style, align = "left" }: { block: FitBlock; style?: CSSProperties; align?: "left" | "center" | "right" }): ReactElement {
  return (
    <div style={{ ...block.style, textAlign: align, ...style }}>
      {block.fit.lines.map((l, i) => (
        <div key={i}>{l}</div>
      ))}
    </div>
  );
}

export function Rule({ width, color, thickness, style }: { width: number; color: string; thickness: number; style?: CSSProperties }): ReactElement {
  return <div style={{ width, height: thickness, background: color, ...style }} />;
}

export function Spacer({ size }: { size: number }): ReactElement {
  return <div style={{ height: size, flex: "0 0 auto" }} />;
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function citationLine(card: SourceCard): string {
  return `${card.publisher} — ${card.title}`;
}

/**
 * A caveat that stays on screen for the whole scene when a figure did not come off a source.
 *
 * Persistent, and that is the entire point: an entrance animation that fades a caveat in and a
 * badge that only shows for the first second are the same thing as no caveat at all, because the
 * frame a viewer screenshots or a clip someone re-cuts will not contain it. It renders at a fixed
 * opacity from frame 0 to the last frame, pinned to the bottom of the safe area rather than
 * flowing with the content, so a scene cannot push it off the card by being long.
 */
export function DataNotice({ classification, background = "paper" }: { classification: DataClassification | undefined; background?: BackgroundRole }): ReactElement | null {
  const { theme, safe, scale, portrait } = useSceneGeometry();
  const notice = classificationNotice(classification);
  if (notice === null) return null;
  const label = theme.type.label;
  // The warning tone is a dark orange: legible on paper, near-invisible on the ink ground a quote
  // card uses, so a dark background takes the light `onInk` accent instead.
  const noticeColor = background === "ink" ? theme.color.onInk.accent : theme.color.tone.warning;
  return (
    <div
      data-data-notice={classification}
      style={{
        position: "absolute",
        left: safe.left,
        // Portrait keeps its lower third for burned-in captions, so the notice sits just above the
        // safe area's own floor rather than at the bottom of the frame.
        top: safe.top + safe.height + Math.round(theme.space[3]! * scale),
        maxWidth: safe.width,
        display: "flex",
        alignItems: "center",
        gap: Math.round(theme.space[2]! * scale),
        fontFamily: FONT_STACK,
        fontSize: Math.max(minTextPx(theme, scale), Math.round(label.size * scale * (portrait ? 1 : 0.9))),
        fontWeight: label.weight,
        letterSpacing: `${label.letterSpacing}em`,
        textTransform: "uppercase",
        color: noticeColor,
        whiteSpace: "nowrap",
      }}
    >
      <span style={{ width: Math.round(10 * scale), height: Math.round(10 * scale), borderRadius: "50%", background: noticeColor, flex: "0 0 auto" }} />
      {notice}
    </div>
  );
}

/**
 * A full-frame still behind a scene, dimmed so type over it keeps its contrast.
 *
 * The dim is not decoration. `legibility.ts` scores contrast against a flat background colour, and
 * a photograph has no single colour — a headline that passes on paper can land on a bright sky and
 * fail. A scrim at :data:`BACKDROP_SCRIM` over the card's own background colour puts a known floor
 * under it, so the same contrast the card was designed for still holds. `ImageScene` is the one
 * caller that asks for `scrim={0}`: there the picture *is* the scene, and nothing is set over it.
 *
 * That floor used to be laid flat over the whole frame, and it cost the picture everything. A
 * title card generated over a photograph of a bee on lavender measured mean HSV saturation 0.219
 * and value 0.570 in the source and 0.063 / 0.837 on the rendered card: 71 % of the colour gone,
 * and a hair above :data:`COLOUR_SAT_MIN` (0.06), the threshold `qc/frame_review.py` uses to call
 * a frame colourless. The card was legible and the photograph was a ghost.
 *
 * The floor is only needed where the type is. So the veil is masked by :data:`SCRIM_FALLOFF`: full
 * strength across the band the content block occupies, easing to :data:`BACKDROP_SCRIM_FLOOR`
 * away from it. Landscape sets its type down the left (`useSceneGeometry().align`), portrait
 * centres it, and `justify` says which way a portrait card leans — so the anchor is read off the
 * layout rather than asked of the caller. A scene carrying a `notice` opts back out to a flat
 * veil: a caveat pinned under the safe area has to be legible wherever the picture is bright.
 *
 * The render is held until the image loads. Without that, a card whose backdrop is still in flight
 * renders on paper for its first frames — a hole at the top of a film, and an intermittent one,
 * which is the worst kind of render bug to chase.
 */
export const BACKDROP_SCRIM = 0.88;
/*
 * 0.88, not the 0.72 this shipped with, and it is solved rather than chosen. The veil is paper
 * over an unknown picture, so the worst pixel any photograph can present is black, and the
 * background under the type is then `scrim x paper`. Solving WCAG AA (4.5:1) for each role
 * against that:
 *
 *     role     colour     clears 4.5 from
 *     ink      #14171C    ~0.30   (never at risk)
 *     muted    #525A66     0.837
 *     accent   #1C5FA8     0.868
 *
 * At 0.72 the muted role — every subtitle, caption, source line and outro paragraph — measured
 * **3.31** against black and fell below 4.5 over 19.5 % of a bee photograph and 36.7 % of a night
 * street. The "known floor" was a floor for the headline only. 0.88 clears every role over every
 * possible pixel, and it costs the picture nothing that the aimed mask does not already give
 * back: the strong veil now covers the type's own band, and the photograph lives in the roll-off.
 */

/** What the veil thins to where no type is set over it. Low enough that the photograph reads as a
 * photograph; not zero, so the picture still sits behind the card rather than in front of it. */
export const BACKDROP_SCRIM_FLOOR = 0.14;

/** Which band of the frame the content block occupies, and therefore where the veil stays full. */
export type ScrimAnchor = "left" | "top" | "middle" | "bottom" | "full";

/** How the veil thins once it is past the type: `[percent further along, how much scrim is left]`. */
const SCRIM_TAIL: ReadonlyArray<readonly [number, number]> = [[0, 1], [6, 0.62], [13, 0.26], [20, 0.06], [25, 0]];

/** Axis and stops per anchor, as `[percent along the axis, fraction of the way to full scrim]`. */
const SCRIM_FALLOFF: Record<Exclude<ScrimAnchor, "full">, { axis: string; stops: ReadonlyArray<readonly [number, number]> }> = {
  // `left` is rebuilt per scene from the measured type width; these stops are only the fallback
  // for a caller that did not measure, and they keep the veil flat right across the safe area.
  left: { axis: "to right", stops: [[0, 1], [96, 1], [100, 0]] },
  top: { axis: "to bottom", stops: [[0, 1], [52, 1], [64, 0.82], [76, 0.5], [88, 0.2], [100, 0]] },
  bottom: { axis: "to top", stops: [[0, 1], [52, 1], [64, 0.82], [76, 0.5], [88, 0.2], [100, 0]] },
  middle: { axis: "to bottom", stops: [[0, 0], [6, 0.3], [16, 0.8], [24, 1], [76, 1], [84, 0.8], [94, 0.3], [100, 0]] },
};

/**
 * A CSS mask that thins the veil away from the type, or `undefined` for a flat one.
 *
 * The mask multiplies the overlay's alpha, so the element keeps `opacity: scrim` and the mask
 * carries the shape — no colour parsing, which matters because `theme.color[background]` is
 * whatever string the theme wrote. Alpha 1 leaves :data:`BACKDROP_SCRIM`; the floor stop is
 * `floor / scrim`, so raising or lowering `scrim` moves both ends together.
 */
export function scrimMask(anchor: ScrimAnchor, scrim: number, plateau?: number, floor: number = BACKDROP_SCRIM_FLOOR): string | undefined {
  if (anchor === "full" || scrim <= 0 || floor >= scrim) return undefined;
  const preset = SCRIM_FALLOFF[anchor];
  const axis = preset.axis;
  // A landscape card sets its type down the left and `plateau` is where that type ends, measured.
  // Guessing instead of measuring is what makes this treatment either useless or unsafe: a guess
  // too narrow leaves a headline's tail on bare photograph, and a guess too wide veils the whole
  // frame again. Absent a measurement the fallback stops keep the old flat veil.
  const stops =
    anchor === "left" && plateau !== undefined
      ? ([[0, 1], ...SCRIM_TAIL.map(([d, v]) => [Math.min(100, plateau + d), v] as const), [100, 0]] as ReadonlyArray<readonly [number, number]>)
      : preset.stops;
  const low = floor / scrim;
  const parts = stops.map(([at, full]) => `rgba(0,0,0,${(low + (1 - low) * full).toFixed(3)}) ${at.toFixed(2)}%`);
  return `linear-gradient(${axis}, ${parts.join(", ")})`;
}

export interface SceneBackdropProps {
  assetId: string | null;
  background: BackgroundRole;
  /** 0 leaves the picture untouched; :data:`BACKDROP_SCRIM` is the default for type over it. */
  scrim?: number;
  /** Where the veil stays full strength. `SceneFrame` reads it off the layout; override to pin it. */
  anchor?: ScrimAnchor;
  /** For `anchor: "left"`, the percentage of the frame the type covers. See :func:`scrimMask`. */
  plateau?: number | undefined;
  /** A scale about the centre, for a still that breathes (see `ImageScene.imageScale`). */
  scale?: number;
  /** Inset the picture into the safe area instead of bleeding to the frame edge. */
  box?: PxBox | undefined;
}

export function SceneBackdrop({ assetId, background, scrim = BACKDROP_SCRIM, anchor = "full", plateau, scale = 1, box }: SceneBackdropProps): ReactElement | null {
  const { bundle, theme, assetUrl } = useSceneEnv();
  const path = assetId === null ? undefined : bundle.assets[assetId];
  const [handle] = useState(() => delayRender(`backdrop:${assetId ?? "none"}`));
  // Releasing the hold is a side effect, so it belongs in an effect and not in the render pass.
  useEffect(() => {
    if (path === undefined) continueRender(handle);
  }, [handle, path]);
  if (assetId === null || path === undefined) return null;
  const mask = scrimMask(anchor, scrim, plateau);
  const frame = box === undefined ? { position: "absolute" as const, inset: 0 } : { position: "absolute" as const, left: box.left, top: box.top, width: box.width, height: box.height };
  return (
    <div data-backdrop={assetId} style={{ ...frame, overflow: "hidden" }}>
      <Img
        src={assetUrl(assetId, path)}
        alt=""
        onLoad={() => {
          continueRender(handle);
        }}
        onError={() => {
          continueRender(handle);
        }}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${scale.toFixed(5)})`,
          transformOrigin: "center center",
        }}
      />
      {scrim > 0 ? <AbsoluteFill data-scrim={anchor} style={{ background: theme.color[background], opacity: scrim, maskImage: mask, WebkitMaskImage: mask }} /> : null}
    </div>
  );
}
