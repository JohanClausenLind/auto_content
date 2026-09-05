// Pinned local fonts only: Inter (body) and Sora (optional display face). Faces come from the
// pinned @fontsource packages, materialized into public/fonts by the render scripts and served by
// Remotion via staticFile(). No remote fonts.
import { FONT_FACES } from "@content-factory/content-ui";
import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

// Module scope inside the browser bundle: loadFont delays the render until each face is ready.
// Skipped in plain Node (unit tests) where FontFace does not exist.
export const interReady: Promise<void> =
  typeof FontFace === "undefined"
    ? Promise.resolve()
    : Promise.all(
        FONT_FACES.map((face) =>
          loadFont({
            family: face.family,
            url: staticFile(`fonts/${face.file}`),
            weight: String(face.weight),
            style: face.style,
            format: "woff2",
          }),
        ),
      ).then(() => undefined);
export const fontsReady = interReady;
