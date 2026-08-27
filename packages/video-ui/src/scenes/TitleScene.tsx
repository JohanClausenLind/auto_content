import type { TitleScene as TitleSpec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { enter, motionFrames, progress } from "../motion";
import { Lines, Rule, SceneFrame, Spacer, useFittedText, useSceneGeometry, type SceneProps } from "./common";

export function TitleScene({ scene }: SceneProps<TitleSpec>): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const title = useFittedText(scene.title.text, "display", safe.width, Math.round(safe.height * 0.5), 4, "paper");
  const subtitle = useFittedText(scene.subtitle?.text ?? "", "subhead", safe.width, Math.round(safe.height * 0.2), 2, "paper", theme.color.muted);
  const ruleWidth = Math.round(120 * scale * progress(frame, 0, f.base, theme.motion.easing.standard));
  return (
    <SceneFrame testId="title">
      <Rule width={Math.max(1, ruleWidth)} thickness={Math.max(2, Math.round(6 * scale))} color={theme.color.accent} />
      <Spacer size={Math.round(theme.space[6]! * scale)} />
      <Lines block={title} style={enter(frame, Math.round(f.fast / 2), f.base, theme, 24 * scale)} />
      {scene.subtitle ? (
        <>
          <Spacer size={Math.round(theme.space[5]! * scale)} />
          <Lines block={subtitle} style={enter(frame, f.fast, f.base, theme, 16 * scale)} />
        </>
      ) : null}
    </SceneFrame>
  );
}
