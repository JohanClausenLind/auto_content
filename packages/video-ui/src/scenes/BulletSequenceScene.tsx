import type { BulletSequenceScene as Spec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { enter, motionFrames } from "../motion";
import { Lines, SceneFrame, Spacer, useFittedText, useSceneGeometry, type SceneProps } from "./common";

/** Frame at which bullet `index` of `count` starts revealing: fixed cadence across the scene. */
export function bulletRevealFrame(index: number, count: number, durationInFrames: number, introFrames: number, tailFrames: number): number {
  const usable = Math.max(1, durationInFrames - introFrames - tailFrames);
  const cadence = Math.max(1, Math.floor(usable / Math.max(1, count)));
  return introFrames + index * cadence;
}

function Bullet({ text, from, marker, gap }: { text: string; from: number; marker: number; gap: number }): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const block = useFittedText(text, "subhead", safe.width - marker - gap, Math.round(safe.height * 0.18), 3, "paper");
  return (
    <div style={{ display: "flex", alignItems: "flex-start", ...enter(frame, from, f.base, theme, 14 * scale) }}>
      <div style={{ width: marker, height: marker, marginTop: Math.round((block.fit.lineHeightPx - marker) / 2), marginRight: gap, background: theme.color.accent, flex: "0 0 auto" }} />
      <Lines block={block} />
    </div>
  );
}

export function BulletSequenceScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps, durationInFrames } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const title = useFittedText(scene.title.text, "headline", safe.width, Math.round(safe.height * 0.25), 3, "paper");
  const marker = Math.max(6, Math.round(14 * scale));
  const gap = Math.round(theme.space[5]! * scale);
  return (
    <SceneFrame testId="bullet_sequence">
      <Lines block={title} style={enter(frame, 0, f.base, theme, 20 * scale)} />
      <Spacer size={Math.round(theme.space[7]! * scale)} />
      {scene.bullets.map((b, i) => (
        <div key={i}>
          {i > 0 ? <Spacer size={Math.round(theme.space[5]! * scale)} /> : null}
          <Bullet text={b.text} from={bulletRevealFrame(i, scene.bullets.length, durationInFrames, f.base, f.base)} marker={marker} gap={gap} />
        </div>
      ))}
    </SceneFrame>
  );
}
