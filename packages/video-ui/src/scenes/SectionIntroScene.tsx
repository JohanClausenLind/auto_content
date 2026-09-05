import type { SectionIntroScene as Spec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { enter, motionFrames } from "../motion";
import { Lines, SceneFrame, Spacer, useFittedText, useSceneGeometry, type SceneProps } from "./common";

export function SectionIntroScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps, align } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const label = useFittedText(scene.label.text, "label", safe.width, Math.round(safe.height * 0.1), 1, "paper");
  const heading = useFittedText(scene.heading.text, "headline", safe.width, Math.round(safe.height * 0.5), 4, "paper");
  return (
    <SceneFrame testId="section_intro">
      <Lines block={label} align={align} style={enter(frame, 0, f.base, theme, 12 * scale)} />
      <Spacer size={Math.round(theme.space[5]! * scale)} />
      <Lines block={heading} align={align} style={enter(frame, Math.round(f.fast / 2), f.base, theme, 24 * scale)} />
    </SceneFrame>
  );
}
