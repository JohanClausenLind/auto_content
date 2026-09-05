/**
 * The template browser, laid out the way ComfyUI lays out its Workflow Templates dialog: a left
 * panel of categories, a header with the page title and a search box, and a grid of cards — each
 * card a live thumbnail of the actual graph (drawn from the graph, so it cannot lie) with the
 * model requirements underneath. Requirements are checked against the real local inventory and
 * the model store, and a missing one installs on one click from the card — the pinned source and
 * the destination live in the Python registry, so the card only has to name the family.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { GraphThumbnail } from "@content-factory/node-graph";
import { comfyModelsQuery, modelCatalogQuery } from "../api/queries";
import { InstallButton } from "../models/ModelStore";
import { workspaceCatalog } from "./catalog";
import {
  downloadCommand,
  requirementState,
  WORKFLOW_TEMPLATES,
  type ModelRequirement,
  type RequirementStatus,
  type WorkflowTemplate,
} from "./templates";

export function CommandLine({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard unavailable (permissions, jsdom): the command is still selectable text.
    }
  };
  return (
    <span className="cf-tpl__command">
      <code>{text}</code>
      <button type="button" className="cf-tpl__copy" onClick={() => void copy()}>
        {copied ? "Copied" : "Copy"}
      </button>
    </span>
  );
}

/**
 * One model requirement. A missing one carries the registry key that installs it, so the row's
 * button is the install itself — the panel used to print a command for the operator to run in a
 * terminal, which is the one thing this page exists to avoid. A requirement with no registry
 * entry still shows the command, because inventing a source would be worse.
 */
export function RequirementRow({
  req,
  status,
}: {
  req: ModelRequirement;
  status: RequirementStatus;
}) {
  const { data: catalog } = useQuery(modelCatalogQuery);
  const job = req.installKey ? catalog?.jobs.find((j) => j.key === req.installKey) : undefined;
  // The store's own verdict wins where it has one (it can see the weight store and the skill
  // envs); the inventory-derived status passed in is the fallback for anything it cannot.
  const fromCatalog = req.installKey ? requirementState(req, catalog, undefined) : "unknown";
  const state: RequirementStatus =
    job?.state === "complete" || job?.state === "already_installed"
      ? "present"
      : fromCatalog !== "unknown"
        ? fromCatalog
        : status;

  const label =
    job?.state === "running"
      ? "installing…"
      : job?.state === "failed"
        ? "install failed"
        : job?.state === "needs_access"
          ? "needs access"
          : state === "present"
            ? "installed"
            : state === "missing"
              ? "missing"
              : "unknown";

  return (
    <li className="cf-tpl__req" data-status={state}>
      <span className="cf-tpl__req-dot" aria-hidden="true" />
      <span className="cf-tpl__req-label">
        {req.label}
        <span className="cf-tpl__req-status">{label}</span>
      </span>
      {state !== "present" && req.installKey && (
        <InstallButton
          itemKey={req.installKey}
          label={req.kind === "skill" ? "Build env" : "Install"}
          job={job}
        />
      )}
      {state !== "present" && !req.installKey && req.kind === "comfy" && (
        <CommandLine text={downloadCommand(req)} />
      )}
      {state !== "present" && !req.installKey && req.kind === "path" && req.setup && (
        <CommandLine text={req.setup} />
      )}
      {(job?.state === "failed" || job?.state === "needs_access") && (
        <span className="cf-tpl__req-error">{job.detail}</span>
      )}
    </li>
  );
}

function TemplateCard({
  template,
  onUse,
}: {
  template: WorkflowTemplate;
  onUse(template: WorkflowTemplate): void;
}) {
  const { data: inventory } = useQuery(comfyModelsQuery);
  const { data: catalog } = useQuery(modelCatalogQuery);
  // Memoized: requirementState scans the store catalog (and, as a fallback, every installed
  // model) and the panel re-renders per search keystroke.
  const statuses = useMemo(
    () => template.models.map((req) => requirementState(req, catalog, inventory)),
    [template, catalog, inventory],
  );
  const missing = statuses.filter((s) => s === "missing").length;
  const graph = useMemo(() => template.build(), [template]);

  return (
    <article className="cf-tpl" aria-label={template.name}>
      <button
        type="button"
        className="cf-tpl__thumb"
        aria-label={`Use template ${template.name}`}
        onClick={() => onUse(template)}
      >
        <GraphThumbnail graph={graph} catalog={workspaceCatalog} width={640} height={288} />
        <span className="cf-tpl__badge" data-ready={missing === 0 || undefined}>
          {missing === 0 ? "Ready" : `${missing} model${missing === 1 ? "" : "s"} to install first`}
        </span>
        <span className="cf-tpl__meta">
          {graph.nodes.length} nodes · {graph.links.length} links
        </span>
      </button>
      <div className="cf-tpl__body">
        <h3 className="cf-tpl__name">{template.name}</h3>
        <p className="cf-tpl__desc">{template.description}</p>
        {template.caveat && <p className="cf-tpl__caveat">{template.caveat}</p>}
        {template.prerequisite && <p className="cf-tpl__prereq">{template.prerequisite}</p>}
        {template.models.length > 0 && (
          <details className="cf-tpl__models" open={missing > 0}>
            <summary>
              Models{" "}
              <span className="cf-tpl__models-count">
                {statuses.filter((s) => s === "present").length}/{template.models.length} installed
              </span>
            </summary>
            <ul className="cf-tpl__reqs" aria-label={`Models for ${template.name}`}>
              {template.models.map((req, index) => (
                <RequirementRow key={index} req={req} status={statuses[index] ?? "unknown"} />
              ))}
            </ul>
          </details>
        )}
        <footer className="cf-tpl__foot">
          <span className="cf-tpl__tags">
            {template.tags.slice(0, 4).map((tag) => (
              <span key={tag} className="cf-tpl__tag">
                {tag}
              </span>
            ))}
          </span>
          <button type="button" className="cf-wsbtn cf-wsbtn--run" onClick={() => onUse(template)}>
            Use template
          </button>
        </footer>
      </div>
    </article>
  );
}

export interface TemplatesPanelProps {
  onUse(template: WorkflowTemplate): void;
  onClose(): void;
}

const CATEGORY_LABELS: Record<string, string> = {
  image: "Image",
  video: "Video",
  publish: "Publish",
};

export function TemplatesPanel({ onUse, onClose }: TemplatesPanelProps) {
  const { data: inventory } = useQuery(comfyModelsQuery);
  const [category, setCategory] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const categories = [...new Set(WORKFLOW_TEMPLATES.map((t) => t.category))];
  const needle = query.trim().toLowerCase();
  const shown = WORKFLOW_TEMPLATES.filter(
    (t) =>
      (category === null || t.category === category) &&
      (needle === "" ||
        t.name.toLowerCase().includes(needle) ||
        t.description.toLowerCase().includes(needle) ||
        t.tags.some((tag) => tag.includes(needle))),
  );

  return (
    <section className="cf-templates" role="dialog" aria-label="Workflow templates">
      <aside className="cf-templates__nav">
        <h2 className="cf-templates__title">
          <span aria-hidden="true">▤</span> Templates
        </h2>
        <nav className="cf-templates__catnav" aria-label="Template categories">
          <ul className="cf-templates__cats">
            <li>
              <button type="button" aria-pressed={category === null} onClick={() => setCategory(null)}>
                All Templates
                <span className="cf-templates__count">{WORKFLOW_TEMPLATES.length}</span>
              </button>
            </li>
            {categories.map((c) => (
              <li key={c}>
                <button type="button" aria-pressed={category === c} onClick={() => setCategory(c)}>
                  {CATEGORY_LABELS[c] ?? c}
                  <span className="cf-templates__count">
                    {WORKFLOW_TEMPLATES.filter((t) => t.category === c).length}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <p className="cf-templates__hint">
          {inventory
            ? `${inventory.models.length} model files detected across ${inventory.roots.filter((r) => r.exists).length} local roots.`
            : "Checking local models…"}
        </p>
      </aside>

      <div className="cf-templates__main">
        <header className="cf-templates__head">
          <h2 className="cf-templates__page">
            {category === null ? "All Templates" : (CATEGORY_LABELS[category] ?? category)}
          </h2>
          <input
            type="search"
            className="cf-templates__search"
            placeholder="Search templates…"
            aria-label="Search templates"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="button" className="cf-templates__close" aria-label="Close templates" onClick={onClose}>
            ✕
          </button>
        </header>
        <div className="cf-templates__grid">
          {shown.map((template) => (
            <TemplateCard key={template.id} template={template} onUse={onUse} />
          ))}
          {shown.length === 0 && <p className="cf-templates__empty">No templates match.</p>}
        </div>
      </div>
    </section>
  );
}
