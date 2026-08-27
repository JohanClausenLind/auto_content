import type {
  DependencyImpact,
  EditOperation,
  InvalidationScope,
} from "@content-factory/content-schema-ts";

import type { ProjectRevision } from "./document.js";
import { revisionHash } from "./hash.js";

export class PreconditionError extends Error {
  constructor(
    readonly op: EditOperation["op"],
    message: string,
  ) {
    super(`${op}: ${message}`);
    this.name = "PreconditionError";
  }
}

/**
 * An inverse step restores the previous revision exactly. Most operations are their own inverse
 * with swapped values; two need private steps because the public operation vocabulary has no
 * "unsuppress" or "remove encoding key" (those must never be proposed by models).
 */
export type InverseStep =
  | { readonly kind: "op"; readonly op: EditOperation }
  | { readonly kind: "unsuppress_deliverable"; readonly deliverable_id: string }
  | {
      readonly kind: "restore_encoding";
      readonly scene_id: string;
      readonly encoding: Readonly<Record<string, string | number | boolean>>;
    };

export interface ApplyResult {
  readonly revision: ProjectRevision;
  readonly inverse: InverseStep;
  readonly impact: DependencyImpact;
}

export interface BatchResult {
  readonly before_hash: string;
  readonly after_hash: string;
  readonly revision: ProjectRevision;
  /** Inverse steps in the order they must be applied to undo (already reversed). */
  readonly inverse: readonly InverseStep[];
  readonly impact: DependencyImpact;
}

const INVALIDATION: Record<EditOperation["op"], readonly InvalidationScope[]> = {
  replace_text_range: ["layout", "render", "originality"],
  reorder_carousel_card: ["layout", "render"],
  change_scene_variant: ["layout", "render"],
  retime_beat: ["timing", "render"],
  update_chart_encoding: ["layout", "render"],
  suppress_deliverable: ["packaging"],
};

/** Apply exactly one operation. Pure: never mutates the input revision. */
export function applyOperation(rev: ProjectRevision, op: EditOperation): ApplyResult {
  switch (op.op) {
    case "replace_text_range": {
      const unit = rev.texts[op.unit_id];
      if (!unit) throw new PreconditionError(op.op, `unknown text unit ${op.unit_id}`);
      if (op.start > op.end || op.end > unit.text.length) {
        throw new PreconditionError(op.op, `range ${op.start}-${op.end} out of bounds`);
      }
      const before = unit.text.slice(op.start, op.end);
      if (op.expected_before != null && op.expected_before !== before) {
        throw new PreconditionError(op.op, "expected_before does not match current text");
      }
      const text = unit.text.slice(0, op.start) + op.replacement + unit.text.slice(op.end);
      const touchesClaim = unit.claim_ids.length > 0;
      return {
        revision: { ...rev, texts: { ...rev.texts, [op.unit_id]: { ...unit, text } } },
        inverse: {
          kind: "op",
          op: {
            op: "replace_text_range",
            unit_id: op.unit_id,
            start: op.start,
            end: op.start + op.replacement.length,
            replacement: before,
            expected_before: op.replacement,
          },
        },
        impact: impact([op.unit_id], INVALIDATION[op.op], touchesClaim),
      };
    }
    case "reorder_carousel_card": {
      const car = rev.carousels[op.deliverable_id];
      if (!car) throw new PreconditionError(op.op, `unknown carousel ${op.deliverable_id}`);
      const from = car.card_ids.indexOf(op.card_id);
      if (from < 0) throw new PreconditionError(op.op, `card ${op.card_id} not in carousel`);
      if (op.to_index >= car.card_ids.length) {
        throw new PreconditionError(op.op, `to_index ${op.to_index} out of range`);
      }
      const ids = [...car.card_ids];
      ids.splice(from, 1);
      ids.splice(op.to_index, 0, op.card_id);
      return {
        revision: {
          ...rev,
          carousels: { ...rev.carousels, [op.deliverable_id]: { ...car, card_ids: ids } },
        },
        inverse: { kind: "op", op: { ...op, to_index: from } },
        impact: impact([op.deliverable_id, op.card_id], INVALIDATION[op.op], false),
      };
    }
    case "change_scene_variant": {
      const scene = rev.scenes[op.scene_id];
      if (!scene) throw new PreconditionError(op.op, `unknown scene ${op.scene_id}`);
      return {
        revision: { ...rev, scenes: { ...rev.scenes, [op.scene_id]: { ...scene, variant: op.variant } } },
        inverse: { kind: "op", op: { ...op, variant: scene.variant } },
        impact: impact([op.scene_id], INVALIDATION[op.op], false),
      };
    }
    case "retime_beat": {
      const scene = rev.scenes[op.scene_id];
      if (!scene) throw new PreconditionError(op.op, `unknown scene ${op.scene_id}`);
      return {
        revision: {
          ...rev,
          scenes: { ...rev.scenes, [op.scene_id]: { ...scene, duration_frames: op.duration_frames } },
        },
        inverse: { kind: "op", op: { ...op, duration_frames: scene.duration_frames } },
        impact: impact([op.scene_id], INVALIDATION[op.op], false),
      };
    }
    case "update_chart_encoding": {
      const scene = rev.scenes[op.scene_id];
      if (!scene) throw new PreconditionError(op.op, `unknown scene ${op.scene_id}`);
      const encoding = { ...scene.encoding, ...op.encoding_patch };
      return {
        revision: { ...rev, scenes: { ...rev.scenes, [op.scene_id]: { ...scene, encoding } } },
        inverse: { kind: "restore_encoding", scene_id: op.scene_id, encoding: scene.encoding },
        impact: impact([op.scene_id], INVALIDATION[op.op], false),
      };
    }
    case "suppress_deliverable": {
      const d = rev.deliverables[op.deliverable_id];
      if (!d) throw new PreconditionError(op.op, `unknown deliverable ${op.deliverable_id}`);
      if (d.suppressed) throw new PreconditionError(op.op, "deliverable already suppressed");
      return {
        revision: {
          ...rev,
          deliverables: {
            ...rev.deliverables,
            [op.deliverable_id]: { ...d, suppressed: true, suppressed_reason: op.reason },
          },
        },
        inverse: { kind: "unsuppress_deliverable", deliverable_id: op.deliverable_id },
        impact: impact([op.deliverable_id], INVALIDATION[op.op], false),
      };
    }
  }
}

function applyInverseStep(rev: ProjectRevision, step: InverseStep): ProjectRevision {
  switch (step.kind) {
    case "op":
      return applyOperation(rev, step.op).revision;
    case "unsuppress_deliverable": {
      const d = rev.deliverables[step.deliverable_id];
      if (!d) throw new PreconditionError("suppress_deliverable", `unknown deliverable ${step.deliverable_id}`);
      return {
        ...rev,
        deliverables: {
          ...rev.deliverables,
          [step.deliverable_id]: { ...d, suppressed: false, suppressed_reason: null },
        },
      };
    }
    case "restore_encoding": {
      const scene = rev.scenes[step.scene_id];
      if (!scene) throw new PreconditionError("update_chart_encoding", `unknown scene ${step.scene_id}`);
      return { ...rev, scenes: { ...rev.scenes, [step.scene_id]: { ...scene, encoding: { ...step.encoding } } } };
    }
  }
}

/** Apply a batch atomically: either every op applies or the error propagates and nothing is returned. */
export function applyBatch(rev: ProjectRevision, ops: readonly EditOperation[]): BatchResult {
  const before_hash = revisionHash(rev);
  let current = rev;
  const inverses: InverseStep[] = [];
  const affected = new Set<string>();
  const scopes = new Set<InvalidationScope>();
  let reopensEvidence = false;
  for (const op of ops) {
    const r = applyOperation(current, op);
    current = r.revision;
    inverses.push(r.inverse);
    r.impact.affected_unit_ids.forEach((u) => affected.add(u));
    r.impact.invalidates.forEach((s) => scopes.add(s));
    reopensEvidence ||= r.impact.reopens_evidence_validation;
  }
  return {
    before_hash,
    after_hash: revisionHash(current),
    revision: current,
    inverse: inverses.reverse(),
    impact: {
      affected_unit_ids: [...affected].sort(),
      invalidates: [...scopes].sort(),
      reopens_evidence_validation: reopensEvidence,
      reopens_preflight_gate: reopensEvidence,
    },
  };
}

/** Undo a batch by applying its inverse steps; the result's hash equals the batch's before_hash. */
export function undoBatch(rev: ProjectRevision, result: BatchResult): { revision: ProjectRevision; after_hash: string } {
  let current = rev;
  for (const step of result.inverse) current = applyInverseStep(current, step);
  return { revision: current, after_hash: revisionHash(current) };
}

function impact(
  units: readonly string[],
  scopes: readonly InvalidationScope[],
  reopensEvidence: boolean,
): DependencyImpact {
  return {
    affected_unit_ids: [...units],
    invalidates: reopensEvidence ? [...scopes, "evidence"] : [...scopes],
    reopens_evidence_validation: reopensEvidence,
    reopens_preflight_gate: reopensEvidence,
  };
}
