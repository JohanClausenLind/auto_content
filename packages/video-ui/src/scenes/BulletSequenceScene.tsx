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

function Bullet({ text, from, marker, gap, width }: { text: string; from: number; marker: number; gap: number; width: number }): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps, portrait } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const block = useFittedText(text, portrait ? "headline" : "subhead", width - marker - gap, Math.round(safe.height * 0.18), 2, "paper");
  return (
    <div style={{ display: "flex", alignItems: "flex-start", width, ...enter(frame, from, f.base, theme, 14 * scale) }}>
      <div style={{ width: marker, height: marker, borderRadius: Math.round(marker * 0.25), marginTop: Math.round((block.fit.lineHeightPx - marker) / 2), marginRight: gap, background: theme.color.accent, flex: "0 0 auto" }} />
      <Lines block={block} />
    </div>
  );
}

/** A heading and its points, revealed one at a time on a fixed cadence. Portrait keeps the
 * points left-aligned inside a centred column so the accent markers line up. */
export function BulletSequenceScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps, durationInFrames, portrait, align } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const title = useFittedText(scene.title.text, portrait ? "display" : "headline", safe.width, Math.round(safe.height * 0.25), 3, "paper");
  const marker = Math.max(6, Math.round((portrait ? 18 : 14) * scale));
  const gap = Math.round(theme.space[5]! * scale);
  const column = portrait ? Math.round(safe.width * 0.88) : safe.width;
  return (
    <SceneFrame testId="bullet_sequence">
      <Lines block={title} align={align} style={enter(frame, 0, f.base, theme, 20 * scale)} />
      <Spacer size={Math.round(theme.space[7]! * scale)} />
      {scene.bullets.map((b, i) => (
        <div key={i} style={{ width: column }}>
          {i > 0 ? <Spacer size={Math.round(theme.space[6]! * scale)} /> : null}
          <Bullet text={b.text} from={bulletRevealFrame(i, scene.bullets.length, durationInFrames, f.base, f.base)} marker={marker} gap={gap} width={column} />
        </div>
      ))}
    </SceneFrame>
  );
}
