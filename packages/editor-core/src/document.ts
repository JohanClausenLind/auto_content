// A minimal project revision model sufficient for the phase-0 spike and grown per phase.
// All units are addressed by opaque ids; nothing here knows about rendering.

export interface TextUnit {
  readonly text: string;
  /** Claim ids linked to this text; editing such a unit reopens evidence validation. */
  readonly claim_ids: readonly string[];
}

export interface SceneUnit {
  readonly variant: string;
  readonly duration_frames: number;
  readonly encoding: Readonly<Record<string, string | number | boolean>>;
}

export interface CarouselUnit {
  readonly card_ids: readonly string[];
}

export interface DeliverableUnit {
  readonly type: string;
  readonly suppressed: boolean;
  readonly suppressed_reason: string | null;
}

export interface ProjectRevision {
  readonly schema_version: 1;
  readonly project_id: string;
  readonly texts: Readonly<Record<string, TextUnit>>;
  readonly scenes: Readonly<Record<string, SceneUnit>>;
  readonly carousels: Readonly<Record<string, CarouselUnit>>;
  readonly deliverables: Readonly<Record<string, DeliverableUnit>>;
}

export function emptyRevision(projectId: string): ProjectRevision {
  return {
    schema_version: 1,
    project_id: projectId,
    texts: {},
    scenes: {},
    carousels: {},
    deliverables: {},
  };
}
