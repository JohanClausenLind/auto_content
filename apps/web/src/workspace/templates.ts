/**
 * Premade workflow templates: a template is a function that builds a complete, valid graph, plus
 * the model files it needs. Requirements are checked against the real local inventory
 * (`GET /v1/comfy/models`, which scans the comfy-cli workspace and configured extra roots), and a
 * missing requirement carries the registry key that installs it (`install_key`, resolved by
 * scripts/export_workflows.py against models/weights.py), so the card can offer a real Install
 * button rather than a command to run somewhere else. No template invents a download URL: the
 * pinned source lives in the Python registry, and a requirement the registry cannot satisfy says
 * so instead of guessing.
 */

import {
  applyOps,
  connectOps,
  emptyGraph,
  makeNode,
  newId,
  type GraphNode,
  type WorkspaceGraph,
} from "@content-factory/node-graph";
import type { ComfyModelsInventory, ModelCatalog } from "../api/types";
import { workspaceCatalog } from "./catalog";
import { WORKFLOW_TEMPLATE_DATA } from "./generated/workflowTemplates";

export type ModelRequirement =
  | {
      /** A file ComfyUI loads from its models/<folder> directory. */
      readonly kind: "comfy";
      readonly label: string;
      /** The models/weights.py registry key that installs this, resolved by the exporter. */
      readonly installKey?: string;
      readonly folder: string; // loras | vae | diffusion_models | text_encoders | ...
      readonly filename: string;
      /** Pinned https source (huggingface.co / civitai.com). Absent = operator supplies it. */
      readonly url?: string;
    }
  | {
      /** A file that lives outside the comfy workspace (skill weights) but is still detectable. */
      readonly kind: "path";
      readonly label: string;
      readonly installKey?: string;
      readonly filename: string;
      readonly pathIncludes: string;
      /** A fetch command, only for the (now empty) case of a weight with no registry entry. */
      readonly setup?: string;
    }
  | {
      /** Served by an isolated skill: its env is built with uv, not downloaded. */
      readonly kind: "skill";
      readonly label: string;
      readonly installKey?: string;
      readonly setup: string;
    };

export interface WorkflowTemplate {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly category: string;
  readonly tags: readonly string[];
  readonly models: readonly ModelRequirement[];
  /** Honest limitation shown on the card (e.g. stages whose executors have not landed yet). */
  readonly caveat?: string;
  /** Something the operator must do before the run works — files to record, a service to start.
   *  Distinct from ``caveat``: the graph is runnable, it just needs its inputs. */
  readonly prerequisite?: string;
  build(): WorkspaceGraph;
}

/** The exact CLI call for a missing comfy-managed file (mirrors models/install.py). */
export function downloadCommand(req: Extract<ModelRequirement, { kind: "comfy" }>): string {
  const url = req.url ?? "<https source on huggingface.co — not pinned yet>";
  return `comfy model download --url ${url} --relative-path models/${req.folder} --filename ${req.filename}`;
}

/** Download suffixes like "(1)" must not hide a present model. */
function normalize(filename: string): string {
  return filename.toLowerCase().replace(/\(\d+\)(?=\.[a-z0-9]+$)/, "");
}

export type RequirementStatus = "present" | "missing" | "unknown";

/**
 * The authoritative state of a requirement: the model store when it knows the family (it scans
 * the weight store and the skill envs, which the ComfyUI inventory cannot see), the inventory
 * scan otherwise.
 */
export function requirementState(
  req: ModelRequirement,
  catalog: ModelCatalog | undefined,
  inventory: ComfyModelsInventory | undefined,
): RequirementStatus {
  if (req.installKey && catalog) {
    const pkg = catalog.packages.find((p) => p.key === req.installKey);
    if (pkg) return pkg.state === "ready" ? "present" : "missing";
    const env = catalog.skill_envs.find((e) => e.key === req.installKey);
    if (env) return env.state === "ready" ? "present" : "missing";
  }
  return requirementStatus(req, inventory);
}

export function requirementStatus(
  req: ModelRequirement,
  inventory: ComfyModelsInventory | undefined,
): RequirementStatus {
  if (req.kind === "skill") return "unknown";
  if (!inventory) return "unknown";
  const wanted = normalize(req.filename);
  if (req.kind === "comfy") {
    return inventory.models.some((m) => m.kind === req.folder && normalize(m.filename) === wanted)
      ? "present"
      : "missing";
  }
  return inventory.models.some(
    (m) => normalize(m.filename) === wanted && `${m.root}/${m.relative_path}`.includes(req.pathIncludes),
  )
    ? "present"
    : "missing";
}

// --- graph building ----------------------------------------------------------------------------

interface Placed {
  readonly type: string;
  readonly x: number;
  readonly y: number;
  readonly values?: Record<string, string | number | boolean>;
  readonly note?: string;
}

/** Build a graph from placed nodes and named links; keys become stable per-template node ids. */
function graphOf(
  name: string,
  nodes: Record<string, Placed>,
  links: readonly [from: string, output: string, to: string, input: string][],
): WorkspaceGraph {
  let graph = emptyGraph(newId("graph"), name);
  const made: Record<string, GraphNode> = {};
  for (const [key, placed] of Object.entries(nodes)) {
    made[key] = makeNode(workspaceCatalog, placed.type, {
      x: placed.x,
      y: placed.y,
      ...(placed.values ? { values: placed.values } : {}),
      ...(placed.note ? { note: placed.note } : {}),
    });
    graph = applyOps(graph, [{ op: "add_node", node: made[key]! }]).graph;
  }
  for (const [from, output, to, input] of links) {
    graph = applyOps(
      graph,
      connectOps(graph, { node: made[from]!.id, slot: output }, { node: made[to]!.id, slot: input }),
    ).graph;
  }
  return graph;
}

// --- the catalogue ----------------------------------------------------------------------------
//
// Every template is generated from workflows/*.yaml by scripts/export_workflows.py. There is no
// hand-written array any more: a workflow used to be defined here AND in the local runner, and the
// two drifted in every way duplicated definitions do. Add a workflow by writing its YAML file and
// running `just schemas`.

interface GeneratedModel {
  readonly kind: "comfy" | "path" | "skill";
  readonly label: string;
  readonly folder?: string;
  readonly filename?: string;
  readonly path_includes?: string;
  readonly skill?: string;
  readonly source_url?: string;
  readonly optional?: boolean;
  readonly install_key?: string;
}

interface GeneratedNode {
  readonly key: string;
  readonly type: string;
  readonly x: number;
  readonly y: number;
  readonly values: Record<string, string | number | boolean>;
  readonly note?: string;
  readonly title?: string;
}

interface GeneratedTemplate {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly category: string;
  readonly tags: readonly string[];
  readonly caveat: string;
  readonly prerequisite: string;
  readonly stages_without_executor: readonly string[];
  readonly models: readonly GeneratedModel[];
  readonly nodes: readonly GeneratedNode[];
  readonly wires: readonly {
    readonly from_key: string;
    readonly from_slot: string;
    readonly to_key: string;
    readonly to_slot: string;
  }[];
  readonly order: readonly string[];
}

/** The generated shape is snake_case because it comes from the Pydantic contract. */
function toRequirement(model: GeneratedModel): ModelRequirement {
  const installKey = model.install_key ? { installKey: model.install_key } : {};
  if (model.kind === "comfy") {
    return {
      kind: "comfy",
      label: model.label,
      folder: model.folder ?? "",
      filename: model.filename ?? "",
      ...installKey,
      ...(model.source_url ? { url: model.source_url } : {}),
    };
  }
  if (model.kind === "path") {
    return {
      kind: "path",
      label: model.label,
      filename: model.filename ?? "",
      pathIncludes: model.path_includes ?? "",
      ...installKey,
      ...(model.source_url ? { setup: model.source_url } : {}),
    };
  }
  return {
    kind: "skill",
    label: model.label,
    ...installKey,
    setup: model.skill ? `cd ${model.skill} && uv sync` : "",
  };
}

export function toTemplate(data: GeneratedTemplate): WorkflowTemplate {
  const caveat = data.caveat || undefined;
  return {
    id: data.id,
    name: data.name,
    description: data.description,
    category: data.category,
    tags: data.tags,
    models: data.models.map(toRequirement),
    ...(caveat ? { caveat } : {}),
    ...(data.prerequisite ? { prerequisite: data.prerequisite } : {}),
    build: () =>
      graphOf(
        data.name,
        Object.fromEntries(
          data.nodes.map((node) => [
            node.key,
            {
              type: node.type,
              x: node.x,
              y: node.y,
              ...(Object.keys(node.values).length ? { values: node.values } : {}),
              ...(node.note ? { note: node.note } : {}),
            },
          ]),
        ),
        data.wires.map(
          (w) => [w.from_key, w.from_slot, w.to_key, w.to_slot] as const,
        ) as readonly [string, string, string, string][],
      ),
  };
}

export const WORKFLOW_TEMPLATES: readonly WorkflowTemplate[] = (
  WORKFLOW_TEMPLATE_DATA as unknown as readonly GeneratedTemplate[]
).map(toTemplate);
