// Compiled Ajv (draft 2020-12) validators for every exported contract.
// The schema JSON files are the artifact exported by `scripts/export_schemas.py`.
import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

import { SCHEMA_NAMES, type SchemaName } from "../generated/index.js";
import ComfyProvenance from "../schema/ComfyProvenance.schema.json";
import ComfyWorkflowPackage from "../schema/ComfyWorkflowPackage.schema.json";
import ControlAsset from "../schema/ControlAsset.schema.json";
import DependencyImpact from "../schema/DependencyImpact.schema.json";
import EditBatch from "../schema/EditBatch.schema.json";
import EditOperation from "../schema/EditOperation.schema.json";
import FixPlan from "../schema/FixPlan.schema.json";
import FrameSpec from "../schema/FrameSpec.schema.json";
import GenerationLock from "../schema/GenerationLock.schema.json";
import HardwareInventory from "../schema/HardwareInventory.schema.json";
import MotionPlan from "../schema/MotionPlan.schema.json";
import RevisionOutcome from "../schema/RevisionOutcome.schema.json";
import RevisionRequest from "../schema/RevisionRequest.schema.json";

export type * from "../generated/index.js";
export { SCHEMA_NAMES };

const SCHEMAS: Record<SchemaName, object> = {
  ComfyProvenance,
  ComfyWorkflowPackage,
  ControlAsset,
  DependencyImpact,
  EditBatch,
  EditOperation,
  FixPlan,
  FrameSpec,
  GenerationLock,
  HardwareInventory,
  MotionPlan,
  RevisionOutcome,
  RevisionRequest,
};

const ajv = new Ajv2020({
  strict: true,
  allErrors: true,
  discriminator: true,
  allowUnionTypes: true,
});
addFormats(ajv);
for (const name of SCHEMA_NAMES) ajv.addSchema(SCHEMAS[name]);

export interface ValidationResult {
  ok: boolean;
  errors: ErrorObject[];
}

export function validatorFor(name: SchemaName): ValidateFunction {
  const id = (SCHEMAS[name] as { $id: string }).$id;
  const fn = ajv.getSchema(id);
  if (!fn) throw new Error(`No compiled validator for ${name}`);
  return fn;
}

export function validate(name: SchemaName, value: unknown): ValidationResult {
  const fn = validatorFor(name);
  const ok = fn(value) as boolean;
  return { ok, errors: ok ? [] : [...(fn.errors ?? [])] };
}

export function assertValid<T>(name: SchemaName, value: unknown): T {
  const result = validate(name, value);
  if (!result.ok) {
    throw new Error(`${name} failed validation: ${ajv.errorsText(result.errors)}`);
  }
  return value as T;
}
