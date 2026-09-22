import type { ExplainerRenderBundle, RenderBundle } from "@content-factory/content-schema-ts";
import { Composition, Still } from "remotion";

import artboardFixture from "../../../fixtures/demo/artboard-bundle.json";
import timelineFixture from "../../../fixtures/demo/timeline-bundle.json";
import explainerFixture from "../../../fixtures/explainer/render/bundle-min.json";
import { ArtboardComposition, calculateArtboardMetadata } from "./compositions/ArtboardComposition";
import { ExplainerComposition, calculateExplainerMetadata } from "./compositions/ExplainerComposition";
import { SmokeTitle, smokeTitleSchema } from "./compositions/SmokeTitle";
import { TimelineComposition, calculateTimelineMetadata } from "./compositions/TimelineComposition";

// Default props are the demo fixtures so `remotion studio` shows something; the render scripts
// always pass a validated bundle as inputProps.
const defaultArtboard = artboardFixture as unknown as RenderBundle;
const defaultTimeline = timelineFixture as unknown as RenderBundle;
const defaultExplainer = explainerFixture as unknown as ExplainerRenderBundle;

// Every composition is deterministic: no Math.random, no CSS transitions, no network fetches.
export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="SmokeTitle"
        component={SmokeTitle}
        schema={smokeTitleSchema}
        durationInFrames={90}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{ title: "Content Factory", subtitle: "offline smoke render", seed: "smoke" }}
      />
      <Still id="Artboard" component={ArtboardComposition} defaultProps={{ bundle: defaultArtboard }} calculateMetadata={calculateArtboardMetadata} />
      <Composition
        id="Timeline"
        component={TimelineComposition}
        defaultProps={{ bundle: defaultTimeline }}
        calculateMetadata={calculateTimelineMetadata}
      />
      <Composition
        id="Explainer"
        component={ExplainerComposition}
        defaultProps={{ bundle: defaultExplainer }}
        calculateMetadata={calculateExplainerMetadata}
      />
    </>
  );
};
