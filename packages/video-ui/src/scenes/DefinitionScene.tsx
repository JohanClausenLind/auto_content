import type { DefinitionScene as Spec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { enter, motionFrames } from "../motion";
import { Lines, Rule, SceneFrame, Spacer, useFittedText, useSceneGeometry, type SceneProps } from "./common";

export function DefinitionScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const kicker = useFittedText("Definition", "label", safe.width, Math.round(safe.height * 0.08), 1, "paper");
  const term = useFittedText(scene.term.text, "headline", safe.width, Math.round(safe.height * 0.25), 3, "paper");
  const definition = useFittedText(scene.definition.text, "body", safe.width, Math.round(safe.height * 0.4), 6, "paper");
  return (
    <SceneFrame testId="definition">
      <Lines block={kicker} style={enter(frame, 0, f.base, theme, 10 * scale)} />
      <Spacer size={Math.round(theme.space[5]! * scale)} />
      <Lines block={term} style={enter(frame, Math.round(f.fast / 2), f.base, theme, 20 * scale)} />
      <Spacer size={Math.round(theme.space[6]! * scale)} />
      <Rule width={Math.round(120 * scale)} thickness={Math.max(2, Math.round(4 * scale))} color={theme.color.rule} />
      <Spacer size={Math.round(theme.space[6]! * scale)} />
      <Lines block={definition} style={enter(frame, f.fast, f.base, theme, 14 * scale)} />
    </SceneFrame>
  );
}
