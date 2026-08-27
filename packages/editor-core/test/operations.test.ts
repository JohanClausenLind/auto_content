import type { EditOperation } from "@content-factory/content-schema-ts";
import { validate } from "@content-factory/content-schema-ts";
import { describe, expect, it } from "vitest";

import {
  applyBatch,
  applyOperation,
  PreconditionError,
  revisionHash,
  undoBatch,
  type ProjectRevision,
} from "../src/index.js";

const base: ProjectRevision = {
  schema_version: 1,
  project_id: "prj_fixture00001",
  texts: {
    txt_hook0000001: { text: "Hello world, this is the hook.", claim_ids: [] },
    txt_fact0000001: { text: "GDP grew 2.1% in 2025.", claim_ids: ["clm_gdp00000001"] },
  },
  scenes: {
    scn_intro000001: { variant: "default", duration_frames: 60, encoding: {} },
    scn_chart000001: { variant: "bar", duration_frames: 120, encoding: { label_size: "medium" } },
  },
  carousels: {
    dlv_carousel0001: { card_ids: ["card_000000001", "card_000000002", "card_000000003"] },
  },
  deliverables: {
    dlv_carousel0001: { type: "carousel", suppressed: false, suppressed_reason: null },
  },
};

const ops: EditOperation[] = [
  { op: "replace_text_range", unit_id: "txt_hook0000001", start: 0, end: 5, replacement: "Today", expected_before: "Hello" },
  { op: "reorder_carousel_card", deliverable_id: "dlv_carousel0001", card_id: "card_000000003", to_index: 0 },
  { op: "retime_beat", scene_id: "scn_intro000001", duration_frames: 72 },
  { op: "change_scene_variant", scene_id: "scn_chart000001", variant: "horizontal_bar" },
  { op: "update_chart_encoding", scene_id: "scn_chart000001", encoding_patch: { label_size: "large", emphasis: "series-2" } },
  { op: "suppress_deliverable", deliverable_id: "dlv_carousel0001", reason: "operator: not this week" },
];

describe("EditorCore", () => {
  it("every test operation validates against the shared EditOperation schema", () => {
    for (const op of ops) expect(validate("EditOperation", op).errors).toEqual([]);
  });

  it("applies a batch and produces a different, deterministic revision hash", () => {
    const a = applyBatch(base, ops);
    const b = applyBatch(base, ops);
    expect(a.after_hash).toBe(b.after_hash);
    expect(a.after_hash).not.toBe(a.before_hash);
    expect(a.revision.texts.txt_hook0000001?.text).toBe("Today world, this is the hook.");
    expect(a.revision.carousels.dlv_carousel0001?.card_ids).toEqual(["card_000000003", "card_000000001", "card_000000002"]);
    expect(a.revision.deliverables.dlv_carousel0001?.suppressed).toBe(true);
    expect(a.revision.scenes.scn_chart000001?.encoding).toEqual({ label_size: "large", emphasis: "series-2" });
  });

  it("undo restores the exact prior revision hash, including removed encoding keys", () => {
    const forward = applyBatch(base, ops);
    const back = undoBatch(forward.revision, forward);
    expect(back.after_hash).toBe(forward.before_hash);
    expect(back.revision).toEqual(base);
  });

  it("redo after undo reproduces the same hash (replay determinism)", () => {
    const forward = applyBatch(base, ops);
    const back = undoBatch(forward.revision, forward);
    const again = applyBatch(back.revision, ops);
    expect(again.after_hash).toBe(forward.after_hash);
  });

  it("never mutates the input revision", () => {
    const snapshot = JSON.stringify(base);
    applyBatch(base, ops);
    expect(JSON.stringify(base)).toBe(snapshot);
  });

  it("a layout-only operation does not reopen evidence; a claim-linked text edit does", () => {
    const layout = applyOperation(base, { op: "retime_beat", scene_id: "scn_intro000001", duration_frames: 10 });
    expect(layout.impact.reopens_evidence_validation).toBe(false);
    expect(layout.impact.invalidates).not.toContain("evidence");
    const factual = applyOperation(base, {
      op: "replace_text_range", unit_id: "txt_fact0000001", start: 8, end: 12, replacement: "3.0%", expected_before: null,
    });
    expect(factual.impact.reopens_evidence_validation).toBe(true);
    expect(factual.impact.invalidates).toContain("evidence");
    expect(factual.impact.reopens_preflight_gate).toBe(true);
  });

  it("rejects operations whose preconditions fail, leaving the batch unapplied", () => {
    expect(() =>
      applyOperation(base, { op: "replace_text_range", unit_id: "txt_hook0000001", start: 0, end: 5, replacement: "X", expected_before: "Nope" }),
    ).toThrow(PreconditionError);
    expect(() => applyOperation(base, { op: "retime_beat", scene_id: "scn_missing00001", duration_frames: 1 })).toThrow(PreconditionError);
    expect(() =>
      applyBatch(base, [ops[0]!, { op: "reorder_carousel_card", deliverable_id: "dlv_carousel0001", card_id: "card_000000003", to_index: 99 }]),
    ).toThrow(PreconditionError);
  });

  it("canonical hashing is key-order independent", () => {
    const a = revisionHash({ b: 1, a: [1, 2, { z: 1, y: 2 }] });
    const b = revisionHash({ a: [1, 2, { y: 2, z: 1 }], b: 1 });
    expect(a).toBe(b);
  });
});
