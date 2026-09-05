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
}

/** Background + safe-area container. Hard cuts: no enter/exit at the frame level. In portrait a
 * thin accent progress rule runs along the top of the safe area for the scene's duration. */
export function SceneFrame({ background = "paper", justify = "center", children, testId, backgroundAssetId = null, notice, backdrop }: SceneFrameProps): ReactElement {
  const { theme, safe, portrait, scale, durationInFrames } = useSceneGeometry();
  const frame = useCurrentFrame();
  const justifyContent = { start: "flex-start", center: "center", end: "flex-end" }[justify];
  const progressWidth = Math.round(safe.width * Math.min(1, frame / Math.max(1, durationInFrames - 1)));
  return (
    <AbsoluteFill data-scene={testId} style={{ background: theme.color[background], fontFamily: FONT_STACK, color: textColor(theme, "body", background) }}>
      <SceneBackdrop assetId={backgroundAssetId} background={background} {...backdrop} />
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
    minSize: Math.max(8, t.min * scale * 0.5),
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
        fontSize: Math.round(label.size * scale * (portrait ? 1 : 0.9)),
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
 * The render is held until the image loads. Without that, a card whose backdrop is still in flight
 * renders on paper for its first frames — a hole at the top of a film, and an intermittent one,
 * which is the worst kind of render bug to chase.
 */
export const BACKDROP_SCRIM = 0.72;

export interface SceneBackdropProps {
  assetId: string | null;
  background: BackgroundRole;
  /** 0 leaves the picture untouched; :data:`BACKDROP_SCRIM` is the default for type over it. */
  scrim?: number;
  /** A scale about the centre, for a still that breathes (see `ImageScene.imageScale`). */
  scale?: number;
  /** Inset the picture into the safe area instead of bleeding to the frame edge. */
  box?: PxBox | undefined;
}

export function SceneBackdrop({ assetId, background, scrim = BACKDROP_SCRIM, scale = 1, box }: SceneBackdropProps): ReactElement | null {
  const { bundle, theme, assetUrl } = useSceneEnv();
  const path = assetId === null ? undefined : bundle.assets[assetId];
  const [handle] = useState(() => delayRender(`backdrop:${assetId ?? "none"}`));
  // Releasing the hold is a side effect, so it belongs in an effect and not in the render pass.
  useEffect(() => {
    if (path === undefined) continueRender(handle);
  }, [handle, path]);
  if (assetId === null || path === undefined) return null;
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
      {scrim > 0 ? <AbsoluteFill style={{ background: theme.color[background], opacity: scrim }} /> : null}
    </div>
  );
}
