import type { CalloutScene as Spec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { enter, motionFrames } from "../motion";
import { Lines, SceneFrame, useFittedText, useSceneGeometry, type SceneProps } from "./common";

export function CalloutScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const bar = Math.max(4, Math.round(10 * scale));
  const gap = Math.round(theme.space[6]! * scale);
  const text = useFittedText(scene.text.text, "headline", safe.width - bar - gap, Math.round(safe.height * 0.6), 6, "surface");
  return (
    <SceneFrame testId="callout" background="surface">
      <div style={{ display: "flex", alignItems: "stretch", ...enter(frame, 0, f.base, theme, 16 * scale) }}>
        <div data-tone={scene.tone} style={{ width: bar, background: theme.color.tone[scene.tone], marginRight: gap, flex: "0 0 auto" }} />
        <Lines block={text} />
      </div>
    </SceneFrame>
  );
}
