import { loadFont } from "@remotion/fonts";
import {
  AbsoluteFill,
  interpolate,
  random,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { z } from "zod";

// Local font, pinned by the lockfile (@fontsource/inter, SIL OFL 1.1). Loaded before any text is
// measured; `loadFont` blocks the render until the face is available.
const fontFamily = "Inter";
// Loaded at module scope inside the browser bundle; skipped in plain Node (unit tests).
const fontReady: Promise<void> =
  typeof FontFace === "undefined"
    ? Promise.resolve()
    : loadFont({
        family: fontFamily,
        url: staticFile("fonts/inter-latin-700-normal.woff2"),
        weight: "700",
      });
void fontReady;

export const smokeTitleSchema = z.object({
  title: z.string(),
  subtitle: z.string(),
  seed: z.string(),
});

export const SmokeTitle: React.FC<z.infer<typeof smokeTitleSchema>> = ({ title, subtitle, seed }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames, width, height } = useVideoConfig();
  const inSeconds = frame / fps;
  const opacity = interpolate(frame, [0, 15, durationInFrames - 15, durationInFrames], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const rise = interpolate(frame, [0, 20], [40, 0], { extrapolateRight: "clamp" });
  // Seeded randomness only: identical inputs produce identical frames.
  const dots = Array.from({ length: 24 }, (_, i) => ({
    x: random(`${seed}-x-${i}`) * width,
    y: random(`${seed}-y-${i}`) * height,
    r: 6 + random(`${seed}-r-${i}`) * 10,
  }));
  return (
    <AbsoluteFill style={{ backgroundColor: "#0f1115", fontFamily, color: "#f2f2f0" }}>
      <svg width={width} height={height} style={{ position: "absolute", inset: 0 }}>
        {dots.map((d, i) => (
          <circle key={i} cx={d.x} cy={d.y + inSeconds * 8} r={d.r} fill="#2f6f8f" opacity={0.25} />
        ))}
      </svg>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", opacity }}>
        <div style={{ fontSize: 112, fontWeight: 700, transform: `translateY(${rise}px)` }}>{title}</div>
        <div style={{ fontSize: 40, marginTop: 24, color: "#b8c4cc" }}>{subtitle}</div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
