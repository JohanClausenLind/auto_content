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
  // Portrait shorts carry burned-in captions in the lower third (compose_video puts the block at 22
  // % from the bottom), so the cards keep their content above that band.
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
  /** A still from `bundle.assets` to sit behind the type, dimmed. */
  backgroundAssetId?: string | null | undefined;
  /** Classification of the data this scene shows, when it shows any. */
  notice?: DataClassification | undefined;
  /** Overrides for the backdrop; `ImageScene` sets `scrim: 0` because the picture is the scene. */
  backdrop?: Omit<SceneBackdropProps, "assetId" | "background"> | undefined;
  /** How far the scene's own type reaches across the safe area, in px — `fitText` measures it. */
  typeWidth?: number | undefined;
}

/** Background + safe-area container. */
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

/** The smallest size the fitter may choose for a role before it truncates instead. */
export function fitFloorPx(theme: ContentTheme, role: TextRole | "display", scale: number): number {
  const t = theme.type[role];
  return Math.max(8, Math.min(t.size * scale, theme.legibility.minFontPx1080 * scale));
}

/** The smallest size *any* text in a scene may be set at, in px for this frame. */
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

/** A caveat that stays on screen for the whole scene when a figure did not come off a source. */
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

/** A full-frame still behind a scene, dimmed so type over it keeps its contrast. */
export const BACKDROP_SCRIM = 0.88;
/* 0.88, not the 0.72 this shipped with, and it is solved rather than chosen. */

/** What the veil thins to where no type is set over it. */
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

/** A CSS mask that thins the veil away from the type, or `undefined` for a flat one. */
export function scrimMask(anchor: ScrimAnchor, scrim: number, plateau?: number, floor: number = BACKDROP_SCRIM_FLOOR): string | undefined {
  if (anchor === "full" || scrim <= 0 || floor >= scrim) return undefined;
  const preset = SCRIM_FALLOFF[anchor];
  const axis = preset.axis;
  // A landscape card sets its type down the left and `plateau` is where that type ends, measured.
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
