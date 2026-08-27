import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { SCHEMA_NAMES, validate, type FixPlan, type MotionPlan, type SchemaName } from "../src/index.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.join(here, "..", "..", "..", "fixtures", "schema");
const valid = JSON.parse(readFileSync(path.join(fixtures, "valid.json"), "utf8")) as Record<
  SchemaName,
  unknown[]
>;
const invalid = JSON.parse(readFileSync(path.join(fixtures, "invalid.json"), "utf8")) as Record<
  SchemaName,
  unknown[]
>;

describe("Pydantic -> JSON Schema -> Ajv roundtrip", () => {
  it("compiles every registered schema", () => {
    expect(SCHEMA_NAMES.length).toBeGreaterThan(0);
    for (const name of SCHEMA_NAMES) expect(() => validate(name, {})).not.toThrow();
  });

  for (const [name, instances] of Object.entries(valid) as [SchemaName, unknown[]][]) {
    it(`accepts Python-emitted ${name} fixtures`, () => {
      for (const inst of instances) {
        const r = validate(name, inst);
        expect(r.errors).toEqual([]);
        expect(r.ok).toBe(true);
      }
    });
  }

  for (const [name, instances] of Object.entries(invalid) as [SchemaName, unknown[]][]) {
    it(`rejects invalid ${name} fixtures`, () => {
      for (const inst of instances) expect(validate(name, inst).ok).toBe(false);
    });
  }

  it("generated types match the fixture shape (compile-time check)", () => {
    const mp = valid.MotionPlan[0] as MotionPlan;
    expect(mp.frame_count).toBe(8);
    expect(mp.subjects[0]?.keyframes[0]?.frame_index).toBe(0);
    const fp = valid.FixPlan[0] as FixPlan;
    expect(fp.operations[0]?.op).toBe("update_chart_encoding");
  });
});
