import type { ComparisonScene as Spec } from "@content-factory/content-schema-ts";
import { formatNumber, refClassification, resolveNumber, type FormattedNumber } from "@content-factory/content-ui";
import type { CSSProperties, ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { useSceneEnv } from "../context";
import { enter, motionFrames } from "../motion";
import {
  Lines,
  Rule,
  SceneFrame,
  Spacer,
  useFittedText,
  useSceneGeometry,
  type FitBlock,
  type SceneProps,
} from "./common";

/** The two sides' figures, or nulls when the scene carries labels only. */
export function comparisonValues(
  scene: Pick<Spec, "left_value" | "right_value">,
  datasets: Parameters<typeof resolveNumber>[1],
): { left: number | null; right: number | null } {
  return {
    left: scene.left_value === null ? null : resolveNumber(scene.left_value, datasets),
    right: scene.right_value === null ? null : resolveNumber(scene.right_value, datasets),
  };
}

/** Two sides, each with an optional figure: side by side in landscape, stacked in portrait. */
/** What `Side` needs from the scene it sits in: geometry, motion and the shared number style. */
type SideLayout = {
  sideWidth: number;
  frame: number;
  f: ReturnType<typeof motionFrames>;
  theme: ReturnType<typeof useSceneEnv>["theme"];
  scale: number;
  numberStyle: CSSProperties;
};

/** One side's rule, label and figure. */
function Side({
  label,
  fmt,
  accent,
  delay,
  layout,
}: {
  label: FitBlock;
  fmt: FormattedNumber;
  accent: string;
  delay: number;
  layout: SideLayout;
}): ReactElement {
  const { sideWidth, frame, f, theme, scale, numberStyle } = layout;
  const shown = fmt.numeral !== "—";
  return (
    <div
      style={{
        width: sideWidth,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        ...enter(frame, delay, f.base, theme, 14 * scale),
      }}
    >
      <Rule width={Math.round(sideWidth * 0.5)} thickness={Math.max(3, Math.round(7 * scale))} color={accent} />
      <Spacer size={Math.round(theme.space[4]! * scale)} />
      <Lines block={label} align="center" />
      {shown ? (
        <>
          <Spacer size={Math.round(theme.space[4]! * scale)} />
          <div style={{ ...numberStyle, color: accent }}>
            {fmt.prefix}
            {fmt.numeral}
            {fmt.unit.length > 0 ? <span style={{ fontSize: "0.5em", marginLeft: 4 * scale, letterSpacing: 0 }}>{fmt.unit}</span> : null}
          </div>
        </>
      ) : null}
    </div>
  );
}

export function ComparisonScene({ scene }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { bundle } = useSceneEnv();
  const { theme, safe, scale, fps, portrait } = useSceneGeometry();
  const f = motionFrames(theme, fps);

  const title = useFittedText(scene.title.text, portrait ? "display" : "headline", safe.width, Math.round(safe.height * 0.2), 3, "paper");
  const gutter = Math.round(theme.space[6]! * scale);
  const sideWidth = portrait ? safe.width : Math.round((safe.width - gutter) / 2);
  const sideHeight = Math.round(safe.height * (portrait ? 0.3 : 0.5));
  const labelRole = portrait ? "headline" : "subhead";
  const leftLabel = useFittedText(scene.left.text, labelRole, sideWidth, Math.round(sideHeight * 0.35), 2, "paper");
  const rightLabel = useFittedText(scene.right.text, labelRole, sideWidth, Math.round(sideHeight * 0.35), 2, "paper");

  const values = comparisonValues(scene, bundle.datasets);
  // The unit comes off the table, not off the scene: a comparison has no unit field.
  const unitOf = (ref: Spec["left_value"]): string => (ref === null ? "" : (bundle.datasets[ref.dataset_id]?.unit ?? ""));
  const leftFmt = formatNumber(values.left, "auto", unitOf(scene.left_value));
  const rightFmt = formatNumber(values.right, "auto", unitOf(scene.right_value));
  // Either side can point at a different table, so the caveat is the weaker of the two: an
  // illustrative figure next to a measured one still makes the comparison illustrative.
  const left = refClassification(scene.left_value, bundle.datasets);
  const right = refClassification(scene.right_value, bundle.datasets);
  const caveat = [left, right].includes("ILLUSTRATIVE")
    ? "ILLUSTRATIVE"
    : [left, right].includes("ESTIMATE")
      ? "ESTIMATE"
      : undefined;

  const numberStyle: CSSProperties = {
    fontFamily: theme.type.number.family,
    fontWeight: theme.type.number.weight,
    fontSize: Math.round(theme.type.number.size * scale * (portrait ? 0.9 : 0.7)),
    color: theme.color.ink,
    lineHeight: 1,
    fontFeatureSettings: '"tnum"',
    whiteSpace: "pre",
  };
  const layout: SideLayout = { sideWidth, frame, f, theme, scale, numberStyle };

  return (
    <SceneFrame testId="comparison" justify="center" notice={caveat}>
      <Lines block={title} align="center" style={enter(frame, 0, f.base, theme, 16 * scale)} />
      <Spacer size={Math.round(theme.space[6]! * scale)} />
      <div
        style={{
          display: "flex",
          flexDirection: portrait ? "column" : "row",
          alignItems: "center",
          justifyContent: "center",
          gap: gutter,
          width: safe.width,
        }}
      >
        <Side label={leftLabel} fmt={leftFmt} accent={theme.color.accent} delay={f.fast} layout={layout} />
        <Side label={rightLabel} fmt={rightFmt} accent={theme.color.muted} delay={f.base} layout={layout} />
      </div>
    </SceneFrame>
  );
}
