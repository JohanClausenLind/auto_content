import { TimelineComposition as VideoTimeline } from "@content-factory/video-ui";
import type { CalculateMetadataFunction } from "remotion";

import "../fonts";
import type { BundleProps } from "./ArtboardComposition";

export const TimelineComposition: React.FC<BundleProps> = ({ bundle }) => <VideoTimeline bundle={bundle} />;

export const calculateTimelineMetadata: CalculateMetadataFunction<BundleProps> = ({ props }) => {
  const timeline = props.bundle.timeline;
  if (!timeline) throw new Error(`RenderBundle ${props.bundle.bundle_id} has no timeline`);
  return { fps: timeline.fps, width: timeline.width, height: timeline.height, durationInFrames: timeline.total_frames };
};
