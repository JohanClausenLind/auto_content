// Ajv validators compiled from the exact schema JSON shipped by @content-factory/content-schema-ts.
// (The TS package exports source .ts; Node scripts load the same JSON files it compiles from.)
import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const require = createRequire(import.meta.url);
const schemaDir = path.join(path.dirname(require.resolve("@content-factory/content-schema-ts/package.json")), "schema");

const ajv = new Ajv2020({ strict: true, allErrors: true, discriminator: true, allowUnionTypes: true });
addFormats(ajv);

/** @type {Map<string, string>} name -> $id */
const ids = new Map();
for (const file of readdirSync(schemaDir).filter((f) => f.endsWith(".schema.json")).sort()) {
  const schema = JSON.parse(readFileSync(path.join(schemaDir, file), "utf8"));
  ajv.addSchema(schema);
  ids.set(file.replace(/\.schema\.json$/, ""), schema.$id);
}

export function validate(name, value) {
  const id = ids.get(name);
  if (!id) throw new Error(`Unknown schema ${name}`);
  const fn = ajv.getSchema(id);
  const ok = fn(value);
  return { ok, errors: ok ? [] : [...(fn.errors ?? [])] };
}

export function assertValid(name, value) {
  const result = validate(name, value);
  if (!result.ok) throw new Error(`${name} failed validation: ${ajv.errorsText(result.errors)}`);
  return value;
}
