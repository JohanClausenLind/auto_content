import type { BigNumberScene as Spec } from "@content-factory/content-schema-ts";
import { fitNumber, fontStackFor, formatNumber, refClassification, resolveNumber, textColor } from "@content-factory/content-ui";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { useSceneEnv } from "../context";
import { enter, motionFrames, progress } from "../motion";
import { Lines, Rule, SceneFrame, Spacer, useFittedText, useSceneGeometry, type SceneProps } from "./common";

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

/** The headline figure: label above, the number counting up, an accent rule drawing under it
 * as the count completes, context below. Portrait centres everything and gives the context a
 * larger role so a phone reads it without leaning in. */
export function BigNumberScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { bundle } = useSceneEnv();
  const { theme, safe, scale, fps, portrait, align } = useSceneGeometry();
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
    family: numStyle.family,
    maxWidth: safe.width,
    maxHeight: Math.round(safe.height * (portrait ? 0.3 : 0.35)),
    preferredSize: numStyle.size * scale * (portrait ? 1.15 : 1),
    minSize: 8,
    lineHeight: numStyle.lineHeight,
    letterSpacing: numStyle.letterSpacing,
  });
  const label = useFittedText(scene.label.text, "label", safe.width, Math.round(safe.height * 0.12), 2, "paper");
  const context = useFittedText(scene.context?.text ?? "", portrait ? "subhead" : "body", safe.width, Math.round(safe.height * 0.22), 3, "paper", textColor(theme, "caption", "paper"));
  const ruleWidth = Math.round(Math.min(safe.width * 0.36, fit.fontSize * 1.6) * progress(frame, Math.round(f.countUp * 0.6), f.base, theme.motion.easing.standard));
  return (
    <SceneFrame testId="big_number" notice={refClassification(scene.value, bundle.datasets)}>
      <Lines block={label} align={align} style={enter(frame, 0, f.base, theme, 12 * scale)} />
      <Spacer size={Math.round(theme.space[5]! * scale)} />
      <div
        data-number-final={final === null ? undefined : String(final)}
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: portrait ? "center" : "flex-start",
          width: safe.width,
          fontFamily: fontStackFor(numStyle.family),
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
      <Spacer size={Math.round(theme.space[4]! * scale)} />
      <Rule width={Math.max(1, ruleWidth)} thickness={Math.max(3, Math.round(8 * scale))} color={theme.color.accent} />
      {scene.context ? (
        <>
          <Spacer size={Math.round(theme.space[6]! * scale)} />
          <Lines block={context} align={align} style={enter(frame, f.base, f.base, theme, 12 * scale)} />
        </>
      ) : null}
    </SceneFrame>
  );
}
