import type { BigNumberScene as Spec } from "@content-factory/content-schema-ts";
import { FONT_STACK, fitNumber, formatNumber, resolveNumber, textColor } from "@content-factory/content-ui";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { useSceneEnv } from "../context";
import { enter, motionFrames, progress } from "../motion";
import { Lines, SceneFrame, Spacer, useFittedText, useSceneGeometry, type SceneProps } from "./common";

function decimalsOf(n: number): number {
  const s = String(n);
  const i = s.indexOf(".");
  return i < 0 ? 0 : Math.min(2, s.length - i - 1);
}

/** Value shown at count-up progress t: same decimal count as the final value, monotone. */
export function countUpValue(final: number, t: number): number {
  const decimals = decimalsOf(final);
  const scaled = final * t;
  const factor = 10 ** decimals;
  return Math.round(scaled * factor) / factor;
}

export function BigNumberScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { bundle } = useSceneEnv();
  const { theme, safe, scale, fps } = useSceneGeometry();
  const f = motionFrames(theme, fps);
  const final = resolveNumber(scene.value, bundle.datasets);
  const t = progress(frame, Math.round(f.fast / 2), f.countUp, theme.motion.easing.decelerate);
  const shown = final === null ? null : countUpValue(final, t);
  const finalFmt = formatNumber(final, "auto", scene.unit);
  const shownFmt = formatNumber(shown, "auto", scene.unit);
  const numStyle = theme.type.number;
  const fit = fitNumber({
    numeral: finalFmt.prefix + finalFmt.numeral,
    unit: finalFmt.unit,
    weight: numStyle.weight,
    maxWidth: safe.width,
    maxHeight: Math.round(safe.height * 0.35),
    preferredSize: numStyle.size * scale,
    minSize: 8,
    lineHeight: numStyle.lineHeight,
    letterSpacing: numStyle.letterSpacing,
  });
  const label = useFittedText(scene.label.text, "label", safe.width, Math.round(safe.height * 0.12), 2, "paper");
  const context = useFittedText(scene.context?.text ?? "", "body", safe.width, Math.round(safe.height * 0.2), 3, "paper", theme.color.muted);
  return (
    <SceneFrame testId="big_number">
      <Lines block={label} style={enter(frame, 0, f.base, theme, 12 * scale)} />
      <Spacer size={Math.round(theme.space[5]! * scale)} />
      <div
        data-number-final={final === null ? undefined : String(final)}
        style={{
          display: "flex",
          alignItems: "baseline",
          fontFamily: FONT_STACK,
          fontWeight: numStyle.weight,
          letterSpacing: `${numStyle.letterSpacing}em`,
          lineHeight: `${fit.heightPx}px`,
          height: fit.heightPx,
          color: textColor(theme, "number", "paper"),
          fontFeatureSettings: '"tnum"',
          whiteSpace: "pre",
          ...enter(frame, 0, f.base, theme, 20 * scale),
        }}
      >
        <span style={{ fontSize: fit.fontSize }}>{shownFmt.prefix + shownFmt.numeral}</span>
        {finalFmt.unit.length > 0 ? <span style={{ fontSize: fit.unitFontSize, marginLeft: Math.round(fit.fontSize * 0.06), letterSpacing: 0 }}>{finalFmt.unit}</span> : null}
      </div>
      {scene.context ? (
        <>
          <Spacer size={Math.round(theme.space[5]! * scale)} />
          <Lines block={context} style={enter(frame, f.base, f.base, theme, 12 * scale)} />
        </>
      ) : null}
    </SceneFrame>
  );
}
