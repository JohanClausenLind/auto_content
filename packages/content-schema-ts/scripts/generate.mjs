// Generate ONE TypeScript declaration module from the exported JSON Schemas (source of truth:
// Pydantic). All per-model `$defs` are merged into a single bundle so every named type is declared
// exactly once; definitions must be identical across files (Pydantic guarantees this).
import { compile } from "json-schema-to-typescript";
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const schemaDir = path.join(here, "..", "schema");
const outDir = path.join(here, "..", "generated");
await mkdir(outDir, { recursive: true });

const files = (await readdir(schemaDir)).filter((f) => f.endsWith(".schema.json")).sort();
const names = [];
const defs = {};
for (const file of files) {
  const name = file.replace(/\.schema\.json$/, "");
  const schema = JSON.parse(await readFile(path.join(schemaDir, file), "utf8"));
  const { $defs = {}, $schema, $id, ...root } = schema;
  void $schema;
  void $id;
  for (const [k, v] of Object.entries($defs)) {
    if (defs[k] && JSON.stringify(defs[k]) !== JSON.stringify(v)) {
      throw new Error(`$defs collision for ${k} between schemas (definitions differ)`);
    }
    defs[k] = v;
  }
  defs[name] = { ...root, title: name };
  names.push(name);
}

const bundle = {
  title: "ContentFactorySchemas",
  type: "object",
  additionalProperties: false,
  properties: Object.fromEntries(names.map((n) => [n, { $ref: `#/$defs/${n}` }])),
  $defs: defs,
};

const ts = await compile(stripPropertyTitles(stripDiscriminator(bundle)), "ContentFactorySchemas", {
  bannerComment:
    "/* eslint-disable */\n// GENERATED from schema/*.schema.json — do not edit.\n// Source of truth: python/content_factory/schemas (run `just schemas`).",
  additionalProperties: false,
  strictIndexSignatures: false,
  unreachableDefinitions: true,
  style: { singleQuote: false, semi: true, printWidth: 100 },
});
await writeFile(path.join(outDir, "types.d.ts"), ts, "utf8");

const index = [
  "// GENERATED — do not edit.",
  'export type * from "./types";',
  "",
  `export const SCHEMA_NAMES = ${JSON.stringify(names)} as const;`,
  "export type SchemaName = (typeof SCHEMA_NAMES)[number];",
  "",
].join("\n");
await writeFile(path.join(outDir, "index.ts"), index, "utf8");
console.log(JSON.stringify({ generated: names.length, defs: Object.keys(defs).length }));

function stripDiscriminator(node) {
  return walk(node, (k) => k === "discriminator");
}

/** Remove `title` from property-level schemas so no helper alias types (Op, End, ...) are emitted. */
function stripPropertyTitles(bundle) {
  const out = structuredClone(bundle);
  for (const def of Object.values(out.$defs)) {
    if (def.properties) for (const p of Object.values(def.properties)) dropTitlesDeep(p);
    if (def.oneOf) def.oneOf.forEach((x) => { if (!x.$ref) dropTitlesDeep(x); });
    if (def.anyOf) def.anyOf.forEach((x) => { if (!x.$ref) dropTitlesDeep(x); });
  }
  return out;
}

function dropTitlesDeep(node) {
  if (Array.isArray(node)) return node.forEach(dropTitlesDeep);
  if (node && typeof node === "object") {
    delete node.title;
    for (const v of Object.values(node)) dropTitlesDeep(v);
  }
}

function walk(node, dropKey) {
  if (Array.isArray(node)) return node.map((n) => walk(n, dropKey));
  if (node && typeof node === "object") {
    const out = {};
    for (const [k, v] of Object.entries(node)) if (!dropKey(k)) out[k] = walk(v, dropKey);
    return out;
  }
  return node;
}
