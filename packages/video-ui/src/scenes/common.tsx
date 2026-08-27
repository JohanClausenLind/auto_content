import type { CompiledScene, SceneSpec, SourceCard } from "@content-factory/content-schema-ts";
import {
  FONT_STACK,
  fitText,
  scaleFor,
  textColor,
  type BackgroundRole,
  type ContentTheme,
  type FitResult,
  type TextRole,
} from "@content-factory/content-ui";
import type { CSSProperties, ReactElement, ReactNode } from "react";
import { AbsoluteFill, useVideoConfig } from "remotion";

import { useSceneEnv } from "../context";
import { safeBox, type PxBox } from "../layout";

export interface SceneProps<S extends SceneSpec = SceneSpec> {
  scene: S;
  compiled: CompiledScene;
}

export interface SceneGeometry {
  width: number;
  height: number;
  fps: number;
  durationInFrames: number;
  scale: number;
  safe: PxBox;
  theme: ContentTheme;
}

export function useSceneGeometry(): SceneGeometry {
  const { width, height, fps, durationInFrames } = useVideoConfig();
  const { theme } = useSceneEnv();
  return { width, height, fps, durationInFrames, scale: scaleFor(width, height), safe: safeBox(theme, width, height), theme };
}

export interface SceneFrameProps {
  background?: BackgroundRole;
  /** Vertical placement of the content block inside the safe area. */
  justify?: "start" | "center" | "end";
  children: ReactNode;
  testId?: string;
}

/** Background + safe-area container. Hard cuts: no enter/exit at the frame level. */
export function SceneFrame({ background = "paper", justify = "center", children, testId }: SceneFrameProps): ReactElement {
  const { theme, safe } = useSceneGeometry();
  const justifyContent = { start: "flex-start", center: "center", end: "flex-end" }[justify];
  return (
    <AbsoluteFill data-scene={testId} style={{ background: theme.color[background], fontFamily: FONT_STACK, color: textColor(theme, "body", background) }}>
      <div style={{ position: "absolute", left: safe.left, top: safe.top, width: safe.width, height: safe.height, display: "flex", flexDirection: "column", justifyContent }}>
        {children}
      </div>
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
      fontFamily: FONT_STACK,
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
