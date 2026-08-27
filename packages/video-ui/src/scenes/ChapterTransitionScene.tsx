import type { ChapterTransitionScene as Spec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { enter, motionFrames, progress } from "../motion";
import { Lines, Rule, SceneFrame, Spacer, useFittedText, useSceneGeometry, type SceneProps } from "./common";

export function ChapterTransitionScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const label = useFittedText(scene.label.text, "display", safe.width, Math.round(safe.height * 0.5), 4, "ink");
  const ruleWidth = Math.round(safe.width * progress(frame, 0, f.slow, theme.motion.easing.standard));
  return (
    <SceneFrame testId="chapter_transition" background="ink">
      <Lines block={label} style={enter(frame, 0, f.base, theme, 24 * scale)} align="left" />
      <Spacer size={Math.round(theme.space[7]! * scale)} />
      <Rule width={Math.max(1, ruleWidth)} thickness={Math.max(2, Math.round(4 * scale))} color={theme.color.onInk.accent} />
    </SceneFrame>
  );
}
