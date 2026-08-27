// Inter is the only font. Faces come from the pinned @fontsource/inter package, materialized into
// public/fonts by the render scripts and served by Remotion via staticFile(). No remote fonts.
import { INTER_FACES, INTER_FAMILY } from "@content-factory/content-ui";
import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

// Module scope inside the browser bundle: loadFont delays the render until each face is ready.
// Skipped in plain Node (unit tests) where FontFace does not exist.
export const interReady: Promise<void> =
  typeof FontFace === "undefined"
    ? Promise.resolve()
    : Promise.all(
        INTER_FACES.map((face) =>
          loadFont({
            family: INTER_FAMILY,
            url: staticFile(`fonts/${face.file}`),
            weight: String(face.weight),
            style: face.style,
            format: "woff2",
          }),
        ),
      ).then(() => undefined);
