import type { SceneSpec } from "@content-factory/content-schema-ts";
import { FONT_STACK, fitText, scaleFor, type ContentTheme } from "@content-factory/content-ui";
import type { ReactElement } from "react";

import { useSceneEnv } from "../context";
import { safeBox } from "../layout";
import { sceneTitle } from "../mapping";
import { useSceneGeometry, type SceneProps } from "./common";

export interface PlaceholderCardProps {
  kind: string;
  title: string;
  sceneId: string;
  width: number;
  height: number;
  theme: ContentTheme;
}

/** Pure (hook-free) placeholder card so any valid plan renders and tests can inspect it. */
export function PlaceholderCard({ kind, title, sceneId, width, height, theme }: PlaceholderCardProps): ReactElement {
  const scale = scaleFor(width, height);
  const safe = safeBox(theme, width, height);
  const inner = Math.round(safe.width - 2 * theme.space[7]! * scale);
  const label = theme.type.label;
  const heading = fitText({
    text: title,
    weight: theme.type.subhead.weight,
    maxWidth: inner,
    maxHeight: Math.round(safe.height * 0.4),
    maxLines: 4,
    preferredSize: theme.type.subhead.size * scale,
    minSize: 8,
    lineHeight: theme.type.subhead.lineHeight,
    letterSpacing: theme.type.subhead.letterSpacing,
  });
  return (
    <div
      data-scene-placeholder={kind}
      data-scene-id={sceneId}
      style={{ position: "absolute", inset: 0, background: theme.color.paper, fontFamily: FONT_STACK, color: theme.color.ink }}
    >
      <div
        style={{
          position: "absolute",
          left: safe.left,
          top: safe.top,
          width: safe.width,
          height: safe.height,
          boxSizing: "border-box",
          padding: Math.round(theme.space[7]! * scale),
          background: theme.color.surface,
          border: `${Math.max(2, Math.round(3 * scale))}px dashed ${theme.color.rule}`,
          borderRadius: theme.radius.md * scale,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
        }}
      >
        <div style={{ fontSize: Math.round(label.size * scale), fontWeight: label.weight, letterSpacing: `${label.letterSpacing}em`, textTransform: "uppercase", color: theme.color.accent, whiteSpace: "nowrap" }}>
          {`placeholder · ${kind}`}
        </div>
        <div style={{ height: Math.round(theme.space[5]! * scale) }} />
        <div style={{ fontSize: heading.fontSize, fontWeight: theme.type.subhead.weight, lineHeight: `${heading.lineHeightPx}px`, letterSpacing: `${theme.type.subhead.letterSpacing}em`, whiteSpace: "pre" }}>
          {heading.lines.map((l, i) => (
            <div key={i}>{l}</div>
          ))}
        </div>
        <div style={{ height: Math.round(theme.space[5]! * scale) }} />
        <div style={{ fontSize: Math.round(theme.type.caption.size * scale), color: theme.color.muted, whiteSpace: "nowrap" }}>{sceneId}</div>
      </div>
    </div>
  );
}

/** Any SceneSpec kind without a dedicated component (and scenes missing from the plan). */
export function PlaceholderScene({ scene, compiled }: { scene: SceneSpec | null; compiled: SceneProps["compiled"] }): ReactElement {
  const { width, height } = useSceneGeometry();
  const { theme } = useSceneEnv();
  return (
    <PlaceholderCard
      kind={scene ? scene.kind : "missing"}
      title={scene ? sceneTitle(scene) : `scene ${compiled.scene_id} is not in the plan`}
      sceneId={compiled.scene_id}
      width={width}
      height={height}
      theme={theme}
    />
  );
}
