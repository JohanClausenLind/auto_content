import type { ImageScene as Spec } from "@content-factory/content-schema-ts";
import type { ReactElement } from "react";
import { useCurrentFrame } from "remotion";

import { useSceneEnv } from "../context";
import { enter, motionFrames } from "../motion";
import { PlaceholderCard } from "./Placeholder";
import { Lines, SceneFrame, useFittedText, useSceneGeometry, type SceneProps } from "./common";

/** How far a slow push/pull travels over the scene, as a fraction of the frame.
 *
 * Six per cent, and small on purpose. The contract's `motion` field exists so a still can breathe
 * rather than sit dead on screen; a bigger move reads as a Ken Burns effect, which draws attention
 * to the camera instead of the picture and — on a photograph that already fills the frame — crops
 * the subject away at the end of the move. It is also the reason the scale is applied about the
 * centre: pushing from a corner would slide the subject out of frame.
 */
export const IMAGE_MOTION_TRAVEL = 0.06;

/** The scale at `t` (0..1) through the scene, for one of the contract's three motions. */
export function imageScale(motion: Spec["motion"], t: number): number {
  const clamped = Math.min(1, Math.max(0, t));
  if (motion === "slow_push") return 1 + IMAGE_MOTION_TRAVEL * clamped;
  // A pull starts wider than the frame and settles into it, so the last frame is the clean one —
  // the same reasoning as `anchor_frames_for`: the end of a move is what a viewer reads.
  if (motion === "slow_pull") return 1 + IMAGE_MOTION_TRAVEL * (1 - clamped);
  return 1;
}

/**
 * A still from the bundle's assets, held for the beat, optionally breathing.
 *
 * `image` is the most common scene kind in this repo's own fixtures — twenty of the twenty-six
 * scenes across `fixtures/story/*.json` — and it had no component, so every one of them rendered
 * as a labelled grey placeholder. The declared `slow_push`/`slow_pull` motions had never been
 * drawn at all.
 *
 * Full-bleed, unlike every other scene here. The safe-area rule keeps a card's content out of the
 * lower third because burned-in captions land there, and for typeset cards that is right — but a
 * photograph inset into 64 % of a phone frame is a photograph with a paper border, and captions
 * read *better* over a picture than over a card. So the still fills the frame and only the scene's
 * own caption stays inside the safe area.
 *
 * A missing asset is a labelled placeholder rather than an empty frame: the film still cuts, and
 * the card names the asset id that was not in the bundle. `RenderBundle.assets` is what `ingest`
 * fills and `video.render.stage_assets` makes servable.
 */
export function ImageScene({ scene, compiled }: SceneProps<Spec>): ReactElement {
  const frame = useCurrentFrame();
  const { bundle, theme } = useSceneEnv();
  const { safe, scale, fps, width, height } = useSceneGeometry();
  const f = motionFrames(theme, fps);

  const caption = useFittedText(
    scene.caption?.text ?? "",
    "source",
    safe.width,
    Math.round(safe.height * 0.1),
    2,
    "ink",
  );

  if (bundle.assets[scene.asset_id] === undefined) {
    return (
      <PlaceholderCard
        kind="image"
        title={`missing asset · ${scene.asset_id}`}
        sceneId={compiled.scene_id}
        width={width}
        height={height}
        theme={theme}
      />
    );
  }

  // Frame-driven rather than time-driven, so the same frame index always produces the same pixels.
  const t = compiled.duration_frames > 1 ? frame / (compiled.duration_frames - 1) : 1;

  return (
    <SceneFrame
      testId="image"
      background="ink"
      justify="end"
      backgroundAssetId={scene.asset_id}
      backdrop={{ scrim: 0, scale: imageScale(scene.motion, t) }}
    >
      {scene.caption !== null && caption.fit.lines.length > 0 ? (
        <div
          style={{
            alignSelf: "flex-start",
            padding: `${Math.round(theme.space[3]! * scale)}px ${Math.round(theme.space[4]! * scale)}px`,
            background: theme.color.ink,
            borderRadius: theme.radius.sm * scale,
          }}
        >
          {/* Shrink-wrapped: the fitted style carries the full measuring width, which would make
              the pill span the safe area regardless of how short the caption is. */}
          <Lines
            block={{ ...caption, style: { ...caption.style, width: caption.fit.widthPx ?? caption.style.width } }}
            style={enter(frame, f.fast, f.base, theme, 6 * scale)}
          />
        </div>
      ) : null}
    </SceneFrame>
  );
}
