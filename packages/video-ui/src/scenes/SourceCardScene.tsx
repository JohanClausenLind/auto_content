import type { SourceCardScene as Spec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { useSceneEnv } from "../context";
import { enter, motionFrames } from "../motion";
import { Lines, Rule, SceneFrame, Spacer, hostOf, useFittedText, useSceneGeometry, type SceneProps } from "./common";

function SourceRow({ primary, secondary, from }: { primary: string; secondary: string; from: number }): ReactElement {
  const frame = useCurrentFrame();
  const { theme, safe, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const a = useFittedText(primary, "body", safe.width, Math.round(safe.height * 0.12), 2, "paper");
  const b = useFittedText(secondary, "caption", safe.width, Math.round(safe.height * 0.08), 1, "paper");
  return (
    <div style={enter(frame, from, f.base, theme, 10 * scale)}>
      <Lines block={a} style={{ fontWeight: 600 }} />
      <Lines block={b} />
    </div>
  );
}

export function SourceCardScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { bundle } = useSceneEnv();
  const { theme, safe, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const label = useFittedText("Sources", "label", safe.width, Math.round(safe.height * 0.08), 1, "paper");
  return (
    <SceneFrame testId="source_card" justify="start">
      <Spacer size={Math.round(safe.height * 0.08)} />
      <Lines block={label} style={enter(frame, 0, f.base, theme, 10 * scale)} />
      <Spacer size={Math.round(theme.space[4]! * scale)} />
      <Rule width={safe.width} thickness={Math.max(1, Math.round(2 * scale))} color={theme.color.rule} />
      <Spacer size={Math.round(theme.space[6]! * scale)} />
      {scene.source_ids.map((id, i) => {
        const card = bundle.sources[id];
        const primary = card ? card.publisher : `unknown source (${id})`;
        const secondary = card ? `${card.title} · ${hostOf(card.url)}` : "not in bundle";
        return (
          <div key={id}>
            {i > 0 ? <Spacer size={Math.round(theme.space[6]! * scale)} /> : null}
            <SourceRow primary={primary} secondary={secondary} from={Math.round(f.fast / 2) + i * f.fast} />
          </div>
        );
      })}
    </SceneFrame>
  );
}
