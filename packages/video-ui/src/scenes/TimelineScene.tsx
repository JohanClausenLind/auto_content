import type { TimelineScene as Spec } from "@content-factory/content-schema-ts";
import { FONT_STACK, fontStackFor, textColor } from "@content-factory/content-ui";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { enter, motionFrames, progress } from "../motion";
import { Lines, minTextPx, SceneFrame, useFittedText, useSceneGeometry } from "./common";

/** Chronology on a spine; events reveal in order as the spine draws. Landscape runs the spine
 * horizontally; portrait runs it down the left edge with the events stacked beside it, which
 * gives each event a full line instead of a squeezed column. */
export function TimelineScene({ scene }: { scene: Spec; compiled: unknown }): ReactElement {
  const frame = useCurrentFrame();
  const { safe, scale, fps, theme, portrait, align } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const t = progress(frame, Math.round(f.fast / 2), f.countUp, theme.motion.easing.decelerate);
  const title = useFittedText(scene.title.text, portrait ? "headline" : "label", safe.width, Math.round(safe.height * 0.16), 2, "paper");
  const ink = textColor(theme, "body", "paper");
  const muted = textColor(theme, "caption", "paper");

  const n = scene.events.length;
  const revealed = Math.max(1, Math.ceil(n * t));

  if (portrait) {
    const spineX = Math.round(20 * scale);
    const dot = Math.round(22 * scale);
    const areaH = Math.round(safe.height * 0.62);
    const row = areaH / Math.max(1, n);
    const dateFont = Math.round(44 * scale);
    const textFont = Math.round(32 * scale);
    return (
      <SceneFrame testId="timeline">
        <Lines block={title} align={align} style={enter(frame, 0, f.base, theme, 10 * scale)} />
        <div style={{ position: "relative", width: Math.round(safe.width * 0.92), height: areaH, marginTop: Math.round(theme.space[7]! * scale) }}>
          <div style={{ position: "absolute", left: spineX - Math.round(2 * scale), top: row / 2, width: Math.max(2, Math.round(4 * scale)), height: Math.max(0, (areaH - row) * t), background: theme.color.accent }} />
          {scene.events.slice(0, revealed).map((event, i) => (
            <div key={i} style={{ position: "absolute", left: 0, top: row * i, height: row, width: "100%", display: "flex", alignItems: "center", ...enter(frame, Math.round((i * f.countUp) / Math.max(1, n)), f.base, theme, 10 * scale) }}>
              <div style={{ width: dot, height: dot, borderRadius: "50%", background: i === n - 1 ? theme.color.accent : theme.color.paper, border: `${Math.max(2, Math.round(4 * scale))}px solid ${theme.color.accent}`, marginLeft: spineX - dot / 2, flex: "0 0 auto" }} />
              <div style={{ marginLeft: Math.round(28 * scale), fontFamily: FONT_STACK }}>
                <div style={{ fontFamily: fontStackFor(theme.type.headline.family), fontWeight: theme.type.headline.weight, fontSize: dateFont, lineHeight: 1.1, color: i === n - 1 ? theme.color.accent : ink }}>{event.date_label}</div>
                <div style={{ fontSize: textFont, lineHeight: 1.25, color: muted, marginTop: Math.round(6 * scale) }}>{event.text.text}</div>
              </div>
            </div>
          ))}
        </div>
      </SceneFrame>
    );
  }

  // The spine sits in the middle of its own box and the labels hang off it, above and below.
  // It used to sit at 42 % of the *safe area* inside a box 70 % of the safe area tall, with the
  // above-labels pushed down by a second fixed fraction — three different references for one
  // arrangement, and the result measured on a 1920x1080 render (2026-09-10) put every mark in
  // the top 28 % of the safe box with 72 % of it empty underneath. Anchoring both label bands to
  // the spine keeps a two-line event tight against the rule wherever the rule is.
  // ...and the box is what the safe area has left under the title, not a fixed 0.7 of it. The
  // fraction left the bottom 24 % of the safe area unusable on top of everything else; the title
  // knows its own height, so there is nothing to guess.
  const plotH = Math.max(
    Math.round(safe.height * 0.5),
    Math.round(safe.height - title.fit.heightPx - 16 * scale),
  );
  const spineY = Math.round(plotH / 2);
  const labelGap = Math.round(24 * scale);
  const band = safe.width / n;
  return (
    <SceneFrame testId="timeline" justify="start">
      <Lines block={title} style={enter(frame, 0, f.base, theme, 10 * scale)} />
      <div style={{ position: "relative", height: plotH, marginTop: 16 * scale }}>
        <div style={{ position: "absolute", top: spineY, left: 0, width: `${(revealed / n) * 100}%`, height: 3 * scale, background: theme.color.accent, transition: "none" }} />
        {scene.events.slice(0, revealed).map((event, i) => (
          <div key={i} style={{ position: "absolute", left: band * i, width: band, top: 0, height: "100%", paddingRight: 12 * scale, boxSizing: "border-box", ...enter(frame, Math.round((i * f.base) / Math.max(1, n)), f.base, theme, 10 * scale) }}>
            <div style={{ position: "absolute", top: spineY - 6 * scale, left: 0, width: 14 * scale, height: 14 * scale, borderRadius: "50%", background: theme.color.accent }} />
            <div style={i % 2 === 0
              ? { position: "absolute", left: 0, right: 12 * scale, bottom: plotH - spineY + labelGap }
              : { position: "absolute", left: 0, right: 12 * scale, top: spineY + labelGap }}>
              <div style={{ fontWeight: 700, fontSize: 26 * scale, color: ink }}>{event.date_label}</div>
              <div style={{ fontSize: Math.max(minTextPx(theme, scale), 22 * scale), color: muted, marginTop: 4 * scale }}>{event.text.text}</div>
            </div>
          </div>
        ))}
      </div>
    </SceneFrame>
  );
}
