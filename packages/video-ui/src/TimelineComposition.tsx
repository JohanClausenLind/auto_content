import type { CompiledScene, RenderBundle, SceneSpec } from "@content-factory/content-schema-ts";
import { FONT_STACK, resolveTheme } from "@content-factory/content-ui";
import { useMemo, type ReactElement } from "react";
import { AbsoluteFill, Sequence } from "remotion";

import { SceneEnvContext } from "./context";
import { mapTimeline } from "./mapping";
import { BigNumberScene } from "./scenes/BigNumberScene";
import { BulletSequenceScene } from "./scenes/BulletSequenceScene";
import { CalloutScene } from "./scenes/CalloutScene";
import { ChapterTransitionScene } from "./scenes/ChapterTransitionScene";
import { DefinitionScene } from "./scenes/DefinitionScene";
import { OutroScene } from "./scenes/OutroScene";
import { PlaceholderScene } from "./scenes/Placeholder";
import { QuoteScene } from "./scenes/QuoteScene";
import { SectionIntroScene } from "./scenes/SectionIntroScene";
import { SourceCardScene } from "./scenes/SourceCardScene";
import { TitleScene } from "./scenes/TitleScene";

export const CONTENT_THEME_FOR_TIMELINES = "editorial";

/** Chooses the scene component by discriminant; unknown kinds get a labelled placeholder. */
export function SceneSwitch({ spec, compiled }: { spec: SceneSpec | null; compiled: CompiledScene }): ReactElement {
  if (!spec) return <PlaceholderScene scene={null} compiled={compiled} />;
  switch (spec.kind) {
    case "title":
      return <TitleScene scene={spec} compiled={compiled} />;
    case "section_intro":
      return <SectionIntroScene scene={spec} compiled={compiled} />;
    case "big_number":
      return <BigNumberScene scene={spec} compiled={compiled} />;
    case "bullet_sequence":
      return <BulletSequenceScene scene={spec} compiled={compiled} />;
    case "source_card":
      return <SourceCardScene scene={spec} compiled={compiled} />;
    case "outro":
      return <OutroScene scene={spec} compiled={compiled} />;
    case "callout":
      return <CalloutScene scene={spec} compiled={compiled} />;
    case "quote":
      return <QuoteScene scene={spec} compiled={compiled} />;
    case "definition":
      return <DefinitionScene scene={spec} compiled={compiled} />;
    case "chapter_transition":
      return <ChapterTransitionScene scene={spec} compiled={compiled} />;
    default:
      return <PlaceholderScene scene={spec} compiled={compiled} />;
  }
}

export interface TimelineCompositionProps {
  bundle: RenderBundle;
}

/** One <Sequence> per CompiledScene; hard cuts only. */
export function TimelineComposition({ bundle }: TimelineCompositionProps): ReactElement {
  const theme = useMemo(() => resolveTheme(CONTENT_THEME_FOR_TIMELINES, bundle.brand), [bundle.brand]);
  const env = useMemo(() => ({ bundle, theme }), [bundle, theme]);
  const scenes = mapTimeline(bundle);
  return (
    <SceneEnvContext.Provider value={env}>
      <AbsoluteFill style={{ background: theme.color.paper, fontFamily: FONT_STACK }}>
        {scenes.map((m) => (
          <Sequence key={m.compiled.scene_id} name={m.compiled.scene_id} from={m.from} durationInFrames={m.durationInFrames}>
            <SceneSwitch spec={m.spec} compiled={m.compiled} />
          </Sequence>
        ))}
      </AbsoluteFill>
    </SceneEnvContext.Provider>
  );
}
