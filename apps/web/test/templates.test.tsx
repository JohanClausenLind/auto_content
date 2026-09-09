import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { parseGraph, validateGraph } from "@content-factory/node-graph";
import { workspaceCatalog } from "../src/workspace/catalog";
import { WORKFLOW_TEMPLATE_DATA } from "../src/workspace/generated/workflowTemplates";
import TRACKED_TEMPLATES from "../../../fixtures/schema/workflow_templates.json";
import {
  downloadCommand,
  requirementStatus,
  WORKFLOW_TEMPLATES,
} from "../src/workspace/templates";
import { authenticated, COMFY_INVENTORY } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

beforeEach(() => {
  window.localStorage.removeItem("cf.workspace.graphs.v1");
  window.localStorage.removeItem("cf.workspace.active.v1");
});

describe("workflow templates", () => {
  it("every template builds a graph the canvas accepts, or says why it cannot", () => {
    // Exactly two kinds of error are allowed in a freshly opened lane, and both are the
    // operator's next move rather than a defect in the definition:
    //
    // 1. a file input with nothing dropped on it — the lane's declared entry point, which must
    //    say what to put there;
    // 2. an unconnected input on a lane that declares a caveat explaining it.
    //
    // Everything else is a wiring or value mistake in the YAML, and there is nowhere left for one
    // to hide: the Python loader refuses an unwired required input outright now.
    for (const template of WORKFLOW_TEMPLATES) {
      const graph = template.build();
      const errors = validateGraph(graph, workspaceCatalog).filter((p) => p.severity === "error");
      const fileInputs = new Set(
        graph.nodes
          .filter((n) => n.type === "input.audio" || n.type === "input.image" || n.type === "input.video")
          .map((n) => n.id),
      );
      const waitingForMaterial = errors.filter((e) => e.node_id !== null && fileInputs.has(e.node_id));
      const unconnected = errors.filter((e) => /is not connected/.test(e.message));
      expect(
        errors.length - unconnected.length - waitingForMaterial.length,
        `${template.id}: ${errors.map((e) => e.message).join("; ")}`,
      ).toBe(0);
      for (const problem of waitingForMaterial) {
        // "dropped file is empty" says what is wrong. A lane opens on this, so it also has to say
        // what to do about it.
        expect(problem.message, `${template.id}: ${problem.message}`).toMatch(/--input|drop/);
        expect(
          template.prerequisite,
          `${template.id} needs material and does not say so in prerequisite`,
        ).toBeTruthy();
      }
      if (unconnected.length > 0) {
        expect(template.caveat, `${template.id} opens with unconnected inputs and no caveat`).toBeTruthy();
      }
      expect(parseGraph(JSON.parse(JSON.stringify(graph)))).toEqual(graph);
      expect(graph.nodes.length).toBeGreaterThan(1);
    }
  });

  it("folds the steps every lane repeats, and a folded group hides nothing that is wrong", () => {
    // The point of a group is that a lane opens readable. The point of THIS test is that folding
    // can never be a way to hide a hole: a group's ports are derived from the links, so an
    // unconnected required input inside a folded group still shows on the folded node.
    const folded = WORKFLOW_TEMPLATES.filter((t) => t.build().groups.length > 0);
    expect(folded.length, "no lane folds anything, so the catalogue reads as raw nodes").toBeGreaterThan(10);
    for (const template of WORKFLOW_TEMPLATES) {
      const graph = template.build();
      const owner = new Map<string, string>();
      for (const group of graph.groups) {
        expect(group.members.length, `${template.id}: ${group.name} folds nothing`).toBeGreaterThan(1);
        for (const member of group.members) {
          expect(graph.nodes.some((n) => n.id === member)).toBe(true);
          expect(owner.has(member), `${template.id}: ${member} is in two groups`).toBe(false);
          owner.set(member, group.id);
        }
      }
      // Every lane ends in the same three steps, so every lane should fold them.
      const hasDelivery = graph.nodes.some((n) => n.type === "compile_destination_packages");
      if (hasDelivery) {
        expect(graph.groups.length, `${template.id} folds nothing`).toBeGreaterThan(0);
      }
    }
  });

  it("offers every lane the backend defines, and every name is general", () => {
    const ids = WORKFLOW_TEMPLATES.map((t) => t.id);
    // Parity, not a floor. `fixtures/schema/workflow_templates.json` is the tracked contract that
    // `scripts/export_workflows.py` writes from `workflows/*.yaml`, and the Python side asserts
    // its ids equal the definition ids exactly. Comparing against it here closes the loop: a lane
    // that reaches the contract but not the panel the operator clicks now fails on this side too,
    // where a `>= 10` floor would have shrugged at a dropped lane.
    const contractIds = TRACKED_TEMPLATES.templates.map((t: { id: string }) => t.id).sort();
    expect([...ids].sort()).toEqual(contractIds);
    for (const id of [
      "single-image",
      "image-set",
      "image-upscale",
      "picture-story",
      "photo-sequence-video",
      "image-to-video",
      "scene-controlled-video",
      "hybrid-video",
      "narrated-video",
      "silent-video",
      "single-clip-post",
      "voice-over-track",
      "audio-restore",
      "video-finish",
    ]) {
      expect(ids, `${id} missing from the catalogue`).toContain(id);
    }
    // A workflow is named for what it does to the material, never for one subject.
    for (const template of WORKFLOW_TEMPLATES) {
      expect(`${template.id} ${template.name}`.toLowerCase()).not.toMatch(/love|romance/);
    }

    const scene = WORKFLOW_TEMPLATES.find((t) => t.id === "scene-controlled-video")!.build();
    expect(scene.nodes.map((n) => n.type)).toEqual(
      expect.arrayContaining(["plan_shots", "compile_controls", "generate_anchor", "generate_video"]),
    );

    const hybrid = WORKFLOW_TEMPLATES.find((t) => t.id === "hybrid-video")!.build();
    expect(hybrid.nodes.map((n) => n.type)).toEqual(
      expect.arrayContaining(["route_shots", "render_scenes", "compile_controls", "generate_video", "compose_video"]),
    );
    // both picture branches feed the one compose node
    const compose = hybrid.nodes.find((n) => n.type === "compose_video")!;
    const feeders = hybrid.links.filter((l) => l.to_node === compose.id).map((l) => l.to_slot);
    expect(feeders).toEqual(expect.arrayContaining(["frames", "clips", "routing", "audio"]));

    const social = WORKFLOW_TEMPLATES.find((t) => t.id === "single-clip-post")!.build();
    expect(social.nodes.some((n) => n.type === "publish.social")).toBe(true);

    // A lane with a voice and a lane deliberately without one.
    const silent = WORKFLOW_TEMPLATES.find((t) => t.id === "silent-video")!.build();
    expect(silent.nodes.some((n) => n.type === "synthesize_narration" || n.type === "voice_over")).toBe(false);
    expect(silent.nodes.some((n) => n.type === "select_music")).toBe(true);
  });

  it("matches requirements against the inventory, ignoring download suffixes like (1)", () => {
    const ltx = WORKFLOW_TEMPLATES.find((t) => t.id === "image-to-video")!;
    const comfyReqs = ltx.models.filter((m) => m.kind === "comfy");
    expect(comfyReqs.length).toBeGreaterThan(0);
    for (const req of comfyReqs) {
      expect(requirementStatus(req, COMFY_INVENTORY), req.label).toBe("present");
    }
    // Stated inline rather than borrowed from a template, so the assertion does not depend on
    // which lane currently happens to want something absent.
    const absent = {
      kind: "comfy" as const,
      label: "not on this machine",
      folder: "loras",
      filename: "nothing-here.safetensors",
    };
    expect(requirementStatus(absent, COMFY_INVENTORY)).toBe("missing");
    expect(requirementStatus(absent, undefined)).toBe("unknown");
    expect(requirementStatus({ kind: "skill", label: "s", setup: "x" }, COMFY_INVENTORY)).toBe("unknown");
  });

  it("emits comfy-cli download commands in the allowlisted shape", () => {
    const command = downloadCommand({
      kind: "comfy",
      label: "x",
      folder: "loras",
      filename: "style.safetensors",
      url: "https://huggingface.co/acme/style/resolve/main/style.safetensors",
    });
    expect(command).toBe(
      "comfy model download --url https://huggingface.co/acme/style/resolve/main/style.safetensors --relative-path models/loras --filename style.safetensors",
    );
    // without a pinned URL the command never invents one
    expect(downloadCommand({ kind: "comfy", label: "x", folder: "vae", filename: "v.safetensors" })).toContain(
      "not pinned",
    );
  });
});

describe("templates panel", () => {
  it("opens from the workspace, shows live model status, and applies a template as a new tab", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    renderApp("/workspace");
    await screen.findAllByText("Campaign Brief");

    await user.click(screen.getByRole("button", { name: "Templates" }));
    const panel = await screen.findByRole("dialog", { name: "Workflow templates" });
    expect(within(panel).getByText(/model files detected/)).toBeInTheDocument();

    // the image-to-video card knows its comfy models are installed
    const ltx = within(panel).getByRole("article", { name: "Image to video" });
    // The LTX comfy files are in the fixture inventory, so the card shows them installed. Its
    // skill and path requirements are not file-detectable from the browser, so the card may still
    // report some as wanted; the assertion is that detection works, not that nothing is missing.
    expect(within(ltx).getAllByText("installed").length).toBeGreaterThan(0);

    await user.click(within(ltx).getByRole("button", { name: "Use template" }));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: /Image to video/ })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    // the template's nodes are on the canvas and persisted
    expect((await screen.findAllByText("Image to Video (LTX-2.5)")).length).toBeGreaterThan(0);
    const stored = JSON.parse(window.localStorage.getItem("cf.workspace.graphs.v1") ?? "[]") as {
      nodes: { type: string }[];
    }[];
    expect(stored.some((g) => g.nodes.some((n) => n.type === "generate_video"))).toBe(true);
  });
});

describe("picture story template", () => {
  it("wires the staging to the drawings and the recordings to the mix, and names its inputs", () => {
    const template = WORKFLOW_TEMPLATES.find((x) => x.id === "picture-story")!;
    const graph = template.build();
    const byType = new Map(graph.nodes.map((n) => [n.type, n]));

    // The chain that makes the bodies consistent: retrieval picks the captured take, the Blender
    // skeleton carries it, and the drawing is conditioned on that. --shots still overrides the
    // whole plan with a hand-authored fixture.
    expect(byType.get("plan_shots")!.values).toMatchObject({ planner: "reference" });
    expect(byType.has("find_reference")).toBe(true);
    expect(byType.get("find_reference")!.values).toMatchObject({ affection: "affection" });
    expect(byType.get("compile_controls")!.values).toMatchObject({
      compiler: "blender",
      skeleton: true,
    });
    expect(byType.get("generate_anchor")!.values.model).toBe("hidream-o1");
    expect(String(byType.get("generate_anchor")!.values.style).length).toBeGreaterThan(20);

    // The voice is recorded, never synthesized: no TTS node in this graph.
    expect(byType.has("voice_over")).toBe(true);
    expect(byType.has("synthesize_narration")).toBe(false);
    expect(byType.get("voice_over")!.values.takes_dir).toBe("takes");

    // Both review gates are on the canvas, not only in the runner. That asymmetry was the bug
    // this refactor removed: the canvas used to ship the film without the human gates.
    expect(byType.has("review_assets")).toBe(true);
    expect(byType.has("review_frames")).toBe(true);

    // Sound reaches the mix, and the mix reaches the cut.
    const sfxToMix = graph.links.some(
      (l) => l.from_node === byType.get("sound_design")!.id && l.to_node === byType.get("mix_audio")!.id,
    );
    expect(sfxToMix).toBe(true);
    const mixToCut = graph.links.some(
      (l) => l.from_node === byType.get("mix_audio")!.id && l.to_node === byType.get("compose_video")!.id,
    );
    expect(mixToCut).toBe(true);

    // The card tells the operator what to supply before pressing Run.
    expect(template.prerequisite).toBeTruthy();
    expect(template.caveat).toBeUndefined();
  });

  it("carries the stills variant as a parameter rather than a second workflow", () => {
    // Two near-identical definitions were the duplication this catalogue exists to avoid.
    const ids = WORKFLOW_TEMPLATES.map((t) => t.id);
    expect(ids).not.toContain("picture-story-stills");
    const motion = WORKFLOW_TEMPLATES.find((t) => t.id === "picture-story")!
      .build()
      .nodes.find((n) => n.type === "generate_video")!;
    expect(motion.values).toHaveProperty("motion");
  });
});

describe("template runnability honesty", () => {
  it("a lane using a stage with no executor must say so in its caveat", () => {
    // Derived, not hardcoded. This list used to be a literal `["ingest"]` mirroring python's
    // STAGE_EXECUTORS by hand, and it went stale the moment ingest got an executor: every lane
    // using it was then asked for a caveat about a stage that runs. `stages_without_executor` is
    // computed per template by scripts/export_workflows.py from the real STAGE_EXECUTORS.
    for (const template of WORKFLOW_TEMPLATE_DATA) {
      const blocked = template.stages_without_executor;
      if (blocked.length > 0) {
        expect(template.caveat, `${template.id} needs a caveat`).toBeTruthy();
        for (const stage of blocked) {
          expect(template.caveat, `${template.id} caveat should name ${stage}`).toContain(stage);
        }
      }
    }
  });

  it("no caveat is a placeholder", () => {
    for (const template of WORKFLOW_TEMPLATES) {
      if (template.caveat !== undefined) {
        expect(template.caveat.length, `${template.id}`).toBeGreaterThan(20);
        expect(template.caveat.toLowerCase()).not.toMatch(/^tbd|^todo|^n\/a/);
      }
    }
  });
});
