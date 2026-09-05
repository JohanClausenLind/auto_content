import type { OutroScene as Spec } from "@content-factory/content-schema-ts";
import { FONT_STACK, textColor } from "@content-factory/content-ui";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { enter, motionFrames } from "../motion";
import { Lines, SceneFrame, Spacer, useFittedText, useSceneGeometry, type SceneProps } from "./common";

/** Closing card: the text (sources, sign-off) and a call to action set as a solid accent pill. */
export function OutroScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps, portrait, align } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const text = useFittedText(scene.text.text, portrait ? "subhead" : "headline", safe.width, Math.round(safe.height * 0.4), 5, "paper", textColor(theme, "caption", "paper"));
  const cta = useFittedText(scene.cta?.text ?? "", portrait ? "headline" : "subhead", Math.round(safe.width * 0.8), Math.round(safe.height * 0.2), 2, "ink", theme.color.paper);
  const padY = Math.round(theme.space[5]! * scale);
  const padX = Math.round(theme.space[7]! * scale);
  return (
    <SceneFrame testId="outro" backgroundAssetId={scene.background_asset_id}>
      {scene.cta ? (
        <div
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            padding: `${padY}px ${padX}px`,
            borderRadius: theme.radius.full,
            background: theme.color.accent,
            fontFamily: FONT_STACK,
            ...enter(frame, Math.round(f.fast / 2), f.base, theme, 16 * scale),
          }}
        >
          <Lines block={{ ...cta, style: { ...cta.style, width: cta.fit.widthPx ?? cta.style.width } }} align="center" />
        </div>
      ) : null}
      <Spacer size={Math.round(theme.space[7]! * scale)} />
      <Lines block={text} align={align} style={enter(frame, f.base, f.base, theme, 12 * scale)} />
    </SceneFrame>
  );
}
