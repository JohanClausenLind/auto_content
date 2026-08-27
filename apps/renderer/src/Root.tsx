import { Composition } from "remotion";

import { SmokeTitle, smokeTitleSchema } from "./compositions/SmokeTitle";

// Every composition is deterministic: no Math.random, no CSS transitions, no network fetches.
export const RemotionRoot: React.FC = () => {
  return (
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
  );
};
