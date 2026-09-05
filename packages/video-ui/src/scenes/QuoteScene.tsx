import type { QuoteScene as Spec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { useSceneEnv } from "../context";
import { enter, motionFrames } from "../motion";
import { Lines, Rule, SceneFrame, Spacer, citationLine, useFittedText, useSceneGeometry, type SceneProps } from "./common";

export function QuoteScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { bundle } = useSceneEnv();
  const { theme, safe, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const card = bundle.sources[scene.source_id];
  const quote = useFittedText(`“${scene.quote.text}”`, "headline", safe.width, Math.round(safe.height * 0.5), 6, "ink");
  const attribution = useFittedText(`— ${scene.attribution.text}`, "subhead", safe.width, Math.round(safe.height * 0.12), 2, "ink", theme.color.onInk.muted);
  const source = useFittedText(card ? `${theme.citation.prefix}: ${citationLine(card)}` : `${theme.citation.prefix}: ${scene.source_id}`, "source", safe.width, Math.round(safe.height * 0.08), 2, "ink");
  return (
    <SceneFrame testId="quote" backgroundAssetId={scene.background_asset_id} background="ink">
      <Rule width={Math.round(120 * scale)} thickness={Math.max(2, Math.round(6 * scale))} color={theme.color.onInk.accent} />
      <Spacer size={Math.round(theme.space[6]! * scale)} />
      <Lines block={quote} style={enter(frame, 0, f.base, theme, 20 * scale)} />
      <Spacer size={Math.round(theme.space[6]! * scale)} />
      <Lines block={attribution} style={enter(frame, f.fast, f.base, theme, 12 * scale)} />
      <Spacer size={Math.round(theme.space[5]! * scale)} />
      <Lines block={source} style={enter(frame, f.base, f.base, theme, 8 * scale)} />
    </SceneFrame>
  );
}
