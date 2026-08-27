import type { OutroScene as Spec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { enter, motionFrames } from "../motion";
import { Lines, SceneFrame, Spacer, useFittedText, useSceneGeometry, type SceneProps } from "./common";

export function OutroScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const text = useFittedText(scene.text.text, "headline", safe.width, Math.round(safe.height * 0.4), 4, "paper");
  const cta = useFittedText(scene.cta?.text ?? "", "subhead", safe.width, Math.round(safe.height * 0.2), 2, "paper", theme.color.accent);
  return (
    <SceneFrame testId="outro">
      <Lines block={text} style={enter(frame, 0, f.base, theme, 20 * scale)} />
      {scene.cta ? (
        <>
          <Spacer size={Math.round(theme.space[6]! * scale)} />
          <Lines block={cta} style={enter(frame, f.fast, f.base, theme, 12 * scale)} />
        </>
      ) : null}
    </SceneFrame>
  );
}
