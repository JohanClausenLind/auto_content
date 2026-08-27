// Frame-driven motion helpers. Every function is a pure function of (frame, fixed config).
import type { ContentTheme } from "@content-factory/content-ui";
import { Easing, interpolate } from "remotion";

export type Bezier = readonly [number, number, number, number];

export function msToFrames(ms: number, fps: number): number {
  return Math.max(1, Math.round((ms * fps) / 1000));
}

export function easingFor(points: Bezier): (t: number) => number {
  return Easing.bezier(points[0], points[1], points[2], points[3]);
}

/** 0 → 1 between `from` and `from + durationFrames`, clamped, with a fixed easing curve. */
export function progress(frame: number, from: number, durationFrames: number, easing: Bezier): number {
  return interpolate(frame, [from, from + Math.max(1, durationFrames)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: easingFor(easing),
  });
}

export interface EnterStyle {
  opacity: number;
  transform: string;
}

/** Fade + short rise (no bounce). `risePx` is the starting offset. */
export function enter(frame: number, from: number, durationFrames: number, theme: ContentTheme, risePx: number): EnterStyle {
  const t = progress(frame, from, durationFrames, theme.motion.easing.decelerate);
  const rise = Math.round((1 - t) * risePx * 100) / 100;
  return { opacity: t, transform: `translateY(${rise}px)` };
}

/** Frame counts for the theme's motion durations at the composition fps. */
export function motionFrames(theme: ContentTheme, fps: number): Record<keyof ContentTheme["motion"]["duration"], number> {
  const d = theme.motion.duration;
  return {
    micro: msToFrames(d.micro, fps),
    fast: msToFrames(d.fast, fps),
    base: msToFrames(d.base, fps),
    slow: msToFrames(d.slow, fps),
    countUp: msToFrames(d.countUp, fps),
  };
}
