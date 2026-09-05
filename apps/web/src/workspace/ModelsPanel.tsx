/**
 * The Models panel: what is installed (live scan of the comfy workspace and configured roots,
 * grouped the way ComfyUI's model library groups them), what the workflows still need — with the
 * install button right there, the same one the Models page uses — and a paste-a-link box for a
 * one-off huggingface/civitai file that no workflow declares.
 *
 * The missing list comes from the model store (`/v1/models/catalog`), not from matching filenames
 * in the browser: it knows the weight store and the skill environments, which the ComfyUI
 * inventory scan cannot see, and it knows the pinned source for each one.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { comfyDownloadsQuery, comfyModelsQuery, useStartModelDownload } from "../api/queries";
import type { ComfyModelFile } from "../api/types";
import { MissingModels, useModelStore } from "../models/ModelStore";

const FOLDERS = [
  "checkpoints",
  "diffusion_models",
  "loras",
  "vae",
  "text_encoders",
  "clip_vision",
  "controlnet",
  "upscale_models",
  "embeddings",
] as const;

function humanSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

function filenameFromUrl(url: string): string {
  try {
    const base = decodeURIComponent(new URL(url).pathname.split("/").filter(Boolean).at(-1) ?? "");
    return /^[A-Za-z0-9._ ()-]+$/.test(base) ? base : "";
  } catch {
    return "";
  }
}

function AddFromUrl() {
  const [url, setUrl] = useState("");
  const [folder, setFolder] = useState<string>("loras");
  const download = useStartModelDownload();
  const filename = filenameFromUrl(url);
  const ready = url.startsWith("https://") && filename !== "";

  return (
    <form
      className="cf-models__add"
      onSubmit={(event) => {
        event.preventDefault();
        if (!ready) return;
        download.mutate(
          { url, relative_path: `models/${folder}`, filename },
          { onSuccess: () => setUrl("") },
        );
      }}
    >
      <input
        type="url"
        placeholder="https://huggingface.co/… or https://civitai.com/…"
        aria-label="Model URL"
        value={url}
        onChange={(event) => setUrl(event.target.value)}
      />
      <div className="cf-models__add-row">
        <select aria-label="Model folder" value={folder} onChange={(event) => setFolder(event.target.value)}>
          {FOLDERS.map((f) => (
            <option key={f} value={f}>
              models/{f}
            </option>
          ))}
        </select>
        <button type="submit" className="cf-wsbtn cf-wsbtn--run" disabled={!ready || download.isPending}>
          ⭳ Download
        </button>
      </div>
      {filename && <p className="cf-models__add-name">→ {filename}</p>}
      {download.isError && <p className="cf-models__error">{String(download.error)}</p>}
    </form>
  );
}

export function ModelsPanel() {
  const { data: inventory } = useQuery(comfyModelsQuery);
  const { data: jobs } = useQuery(comfyDownloadsQuery);
  const [query, setQuery] = useState("");
  const [closed, setClosed] = useState<readonly string[]>([]);

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const byKind = new Map<string, ComfyModelFile[]>();
    for (const model of inventory?.models ?? []) {
      if (needle && !model.filename.toLowerCase().includes(needle)) continue;
      const list = byKind.get(model.kind);
      if (list) list.push(model);
      else byKind.set(model.kind, [model]);
    }
    return [...byKind.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [inventory, query]);

  const store = useModelStore();
  const active = (jobs ?? []).filter((j) => j.state === "running");

  const toggle = (kind: string) =>
    setClosed((current) => (current.includes(kind) ? current.filter((k) => k !== kind) : [...current, kind]));

  return (
    <div className="cf-models" aria-label="Models">
      <input
        className="ng-library__search"
        type="search"
        placeholder="Search models…"
        aria-label="Search models"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />

      <div className="cf-models__scroll">
        <section aria-label="Installed models">
          <h3 className="cf-models__heading">
            Installed
            <span className="cf-models__sub">
              {inventory
                ? `${inventory.models.length} files · ${inventory.roots.filter((r) => r.exists).length} roots`
                : "scanning…"}
            </span>
          </h3>
          {groups.map(([kind, files]) => {
            const open = query !== "" || !closed.includes(kind);
            return (
              <div key={kind} className="cf-models__group">
                <button type="button" className="ng-library__heading" aria-expanded={open} onClick={() => toggle(kind)}>
                  <span aria-hidden="true">{open ? "▾" : "▸"}</span> {kind}
                  <span className="ng-library__count">{files.length}</span>
                </button>
                {open && (
                  <ul className="cf-models__list">
                    {files.map((file) => (
                      <li key={`${file.root}/${file.relative_path}`} className="cf-models__row" title={`${file.root}/${file.relative_path}`}>
                        <span className="cf-models__dot" aria-hidden="true" />
                        <span className="cf-models__name">{file.filename}</span>
                        <span className="cf-models__size">{humanSize(file.size_bytes)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
          {inventory && groups.length === 0 && <p className="cf-models__empty">No model files found.</p>}
        </section>

        {(jobs ?? []).length > 0 && (
          <section aria-label="Downloads">
            <h3 className="cf-models__heading">
              Downloads
              {active.length > 0 && <span className="cf-models__sub">{active.length} running</span>}
            </h3>
            <ul className="cf-models__list">
              {(jobs ?? []).map((job) => (
                <li key={job.job_id} className="cf-models__row" data-state={job.state} title={job.detail}>
                  <span className="cf-models__dot" aria-hidden="true" />
                  <span className="cf-models__name">{job.filename}</span>
                  <span className="cf-models__size">{job.state.replaceAll("_", " ")}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <MissingModels catalog={store.data} compact />

        <p className="cf-models__empty">
          <Link to="/models">Open the Models page</Link> for licences, sources, disk use and the
          families no workflow currently asks for.
        </p>

        <section aria-label="Add model from URL">
          <h3 className="cf-models__heading">Add from URL</h3>
          <p className="cf-models__empty">
            huggingface.co and civitai.com only; comfy-cli downloads it in the background.
          </p>
          <AddFromUrl />
        </section>
      </div>
    </div>
  );
}
