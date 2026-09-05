/**
 * The model store, as shared parts.
 *
 * One row type serves the Models page and the workspace's Models panel, because they are the
 * same job in two places: see what a weight family is for, whether it is on this machine, and
 * install it if it is not. The install button posts a registry key to `/v1/models/install` — the
 * browser never names a URL, a folder or a filename, so there is exactly one place (the Python
 * registry) that decides where a weight comes from and where it lands.
 *
 * Every state an operator can be in is a state here: absent, part-downloaded, installing with
 * progress, installed but not linked (the repair case), gated behind terms nobody accepted yet,
 * and the two families with no scriptable source at all — which say so instead of showing a
 * button that cannot work.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  hfAccessQuery,
  modelCatalogQuery,
  useForgetHfToken,
  useInstallModel,
  useRelinkModels,
  useStoreHfToken,
} from "../api/queries";
import type { InstallJob, ModelCatalog, ModelPackage, SkillEnvEntry } from "../api/types";

export function formatBytes(bytes: number): string {
  if (bytes <= 0) return "—";
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(1)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(bytes >= 1e10 ? 0 : 1)} GB`;
  if (bytes >= 1e6) return `${Math.round(bytes / 1e6)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

export function useModelStore() {
  return useQuery(modelCatalogQuery);
}

/** Missing = wanted by a workflow and not fully on disk. Required ones first, then by size. */
export function missingPackages(catalog: ModelCatalog | undefined): ModelPackage[] {
  return (catalog?.packages ?? [])
    .filter((p) => p.state !== "ready" && p.wanted_by.length > 0)
    .sort(
      (a, b) =>
        Number(b.required) - Number(a.required) ||
        Number(b.installable) - Number(a.installable) ||
        a.approx_bytes - b.approx_bytes,
    );
}

export function missingSkillEnvs(catalog: ModelCatalog | undefined): SkillEnvEntry[] {
  return (catalog?.skill_envs ?? []).filter((e) => e.state === "absent" && e.wanted_by.length > 0);
}

function jobFor(key: string, jobs: readonly InstallJob[] | undefined): InstallJob | undefined {
  return jobs?.find((job) => job.key === key);
}

const STATE_LABEL: Record<ModelPackage["state"], string> = {
  ready: "installed",
  partial: "part-downloaded",
  absent: "not installed",
  manual: "manual step",
};

function JobLine({ job }: { job: InstallJob }) {
  const percent =
    job.bytes_expected > 0
      ? Math.min(99, Math.round((job.bytes_done / job.bytes_expected) * 100))
      : 0;
  const step = job.steps.find((s) => s.state === "running") ?? job.steps.at(-1);
  return (
    <div className="cf-store__job" data-state={job.state}>
      {job.state === "running" && (
        <>
          <span
            className="cf-store__bar"
            role="progressbar"
            aria-label={`Installing ${job.name}`}
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <span className="cf-store__barfill" style={{ width: `${Math.max(2, percent)}%` }} />
          </span>
          <span className="cf-store__jobtext">
            {step?.label ?? "installing"} · {formatBytes(job.bytes_done)}
            {job.bytes_expected > 0 ? ` of ~${formatBytes(job.bytes_expected)}` : ""}
          </span>
        </>
      )}
      {job.state === "needs_access" && <span className="cf-store__jobtext cf-store__jobtext--warn">{job.detail}</span>}
      {job.state === "failed" && <span className="cf-store__jobtext cf-store__jobtext--bad">{job.detail}</span>}
      {(job.state === "complete" || job.state === "already_installed") && (
        <span className="cf-store__jobtext">{job.detail}</span>
      )}
    </div>
  );
}

export function InstallButton({
  itemKey,
  label,
  disabled,
  job,
}: {
  itemKey: string;
  label: string;
  disabled?: boolean;
  job: InstallJob | undefined;
}) {
  const install = useInstallModel();
  const running = job?.state === "running" || (install.isPending && install.variables === itemKey);
  return (
    <button
      type="button"
      className="cf-button cf-button--primary cf-button--sm"
      disabled={disabled || running}
      onClick={() => install.mutate(itemKey)}
      aria-label={`${label} ${itemKey}`}
    >
      {running ? "Installing…" : label}
    </button>
  );
}

export function PackageRow({
  pkg,
  jobs,
  compact = false,
}: {
  pkg: ModelPackage;
  jobs: readonly InstallJob[] | undefined;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const job = jobFor(pkg.key, jobs);
  const unlinked =
    pkg.state === "ready" &&
    (pkg.index_state === "missing" ||
      pkg.index_state === "broken" ||
      pkg.files.some((f) => f.comfy_folder && !f.comfy_linked));

  return (
    <li className="cf-store__row" data-state={pkg.state} data-unlinked={unlinked || undefined}>
      <div className="cf-store__main">
        <span className="cf-store__dot" aria-hidden="true" />
        <div className="cf-store__ident">
          <span className="cf-store__name">{pkg.name}</span>
          {!compact && <span className="cf-store__purpose">{pkg.purpose}</span>}
          <span className="cf-store__meta">
            <span className="cf-store__state">{STATE_LABEL[pkg.state]}</span>
            {" · "}
            {pkg.state === "ready"
              ? formatBytes(pkg.bytes_on_disk)
              : pkg.approx_bytes > 0
                ? `~${formatBytes(pkg.approx_bytes)}`
                : "size unknown"}
            {pkg.gating !== "no" && (
              <>
                {" · "}
                <span className="cf-store__gated">
                  {pkg.gating === "manual" ? "access must be granted" : "terms to accept"}
                </span>
              </>
            )}
            {pkg.wanted_by.length > 0 && (
              <>
                {" · "}
                {pkg.required ? "required by " : "optional for "}
                {pkg.wanted_by.slice(0, 2).join(", ")}
                {pkg.wanted_by.length > 2 ? ` +${pkg.wanted_by.length - 2}` : ""}
              </>
            )}
          </span>
        </div>
        <div className="cf-store__actions">
          {pkg.installable ? (
            <InstallButton
              itemKey={pkg.key}
              label={pkg.state === "ready" ? (unlinked ? "Repair links" : "Reinstall") : pkg.state === "partial" ? "Resume" : "Install"}
              job={job}
            />
          ) : (
            <span className="cf-store__manualtag">manual</span>
          )}
          <button
            type="button"
            className="cf-button cf-button--ghost cf-button--sm"
            aria-expanded={open}
            aria-label={`Details for ${pkg.name}`}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? "Less" : "Details"}
          </button>
        </div>
      </div>

      {!pkg.installable && <p className="cf-store__manual">{pkg.manual}</p>}
      {job && <JobLine job={job} />}
      {unlinked && !job && (
        <p className="cf-store__warn">
          On disk but not linked where the pipeline looks — Repair links fixes the index symlink and
          ComfyUI&apos;s model folders.
        </p>
      )}

      {open && (
        <div className="cf-store__details">
          <p className="cf-store__purpose">{pkg.purpose}</p>
          {pkg.caveat && <p className="cf-store__caveat">{pkg.caveat}</p>}
          <dl className="cf-store__facts">
            <dt>Licence</dt>
            <dd>{pkg.license}</dd>
            <dt>Store</dt>
            <dd>
              <code>{pkg.store_path}</code>
            </dd>
            {pkg.index_path && (
              <>
                <dt>Index link</dt>
                <dd>
                  <code>{pkg.index_path}</code> ({pkg.index_state})
                </dd>
              </>
            )}
            {pkg.sources.length > 0 && (
              <>
                <dt>Source</dt>
                <dd>
                  <ul className="cf-store__sources">
                    {pkg.sources.map((s, index) => (
                      <li key={index}>
                        <code>{s.repo_id ?? s.url}</code>
                        {s.revision && <span className="cf-store__rev"> @ {s.revision.slice(0, 12)}</span>}
                        {s.note && <span className="cf-store__note"> — {s.note}</span>}
                      </li>
                    ))}
                  </ul>
                </dd>
              </>
            )}
          </dl>
          <ul className="cf-store__files">
            {pkg.files.map((file) => (
              <li key={file.store_rel} data-state={file.state}>
                <code>{file.store_rel}</code>
                <span>
                  {file.state === "present" ? formatBytes(file.size_bytes) : file.state}
                  {file.comfy_folder && (file.comfy_linked ? " · linked into ComfyUI" : " · not linked into ComfyUI")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

export function SkillEnvRow({
  env,
  jobs,
}: {
  env: SkillEnvEntry;
  jobs: readonly InstallJob[] | undefined;
}) {
  const job = jobFor(env.key, jobs);
  return (
    <li className="cf-store__row" data-state={env.state === "ready" ? "ready" : "absent"}>
      <div className="cf-store__main">
        <span className="cf-store__dot" aria-hidden="true" />
        <div className="cf-store__ident">
          <span className="cf-store__name">{env.name}</span>
          <span className="cf-store__purpose">{env.purpose}</span>
          <span className="cf-store__meta">
            <span className="cf-store__state">{env.state === "ready" ? "ready" : "not built"}</span>
            {" · "}
            <code>{env.skill}</code>
            {env.caveat && ` · ${env.caveat}`}
          </span>
        </div>
        <div className="cf-store__actions">
          <InstallButton itemKey={env.key} label={env.state === "ready" ? "Rebuild" : "Build env"} job={job} />
        </div>
      </div>
      {job && <JobLine job={job} />}
    </li>
  );
}

/** The one thing the operator came for: what is missing, and a button that installs all of it. */
export function MissingModels({ catalog, compact = false }: { catalog: ModelCatalog | undefined; compact?: boolean }) {
  const install = useInstallModel();
  const missing = useMemo(() => missingPackages(catalog), [catalog]);
  const envs = useMemo(() => missingSkillEnvs(catalog), [catalog]);
  const installable = missing.filter((p) => p.installable);
  const totalBytes = installable.reduce((sum, p) => sum + Math.max(0, p.approx_bytes - p.bytes_on_disk), 0);
  const enough = !catalog || catalog.store_free_bytes === 0 || catalog.store_free_bytes > totalBytes;

  return (
    <section className="cf-store__section" aria-label="Missing models">
      <header className="cf-store__sectionhead">
        <h2 className="cf-store__heading">
          Missing models
          <span className="cf-store__count">{missing.length + envs.length}</span>
        </h2>
        {installable.length + envs.length > 0 && (
          <button
            type="button"
            className="cf-button cf-button--primary cf-button--sm"
            disabled={install.isPending}
            onClick={() => {
              for (const pkg of installable) install.mutate(pkg.key);
              for (const env of envs) install.mutate(env.key);
            }}
          >
            Install all ({formatBytes(totalBytes)})
          </button>
        )}
      </header>
      {missing.length + envs.length === 0 ? (
        <p className="cf-store__empty">
          Every weight and skill environment the workflows declare is installed on this machine.
        </p>
      ) : (
        <>
          {!enough && (
            <p className="cf-store__warn" role="alert">
              {formatBytes(totalBytes)} to download but only {formatBytes(catalog?.store_free_bytes ?? 0)} free
              in {catalog?.store}.
            </p>
          )}
          <ul className="cf-store__list">
            {missing.map((pkg) => (
              <PackageRow key={pkg.key} pkg={pkg} jobs={catalog?.jobs} compact={compact} />
            ))}
            {envs.map((env) => (
              <SkillEnvRow key={env.key} env={env} jobs={catalog?.jobs} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

/**
 * Hugging Face access. Three of the families this stack needs are gated — LTX-2.5 behind
 * Lightricks' terms, Stable Audio behind Stability's, SAM 3.1 behind a human decision at Meta —
 * and without a token their install answers 401 and nothing here can fix it. So the token is
 * enterable from the page that needs it, sealed in the same vault as every other credential, and
 * never read back into the browser.
 */
function HuggingFaceAccessRow() {
  const access = useQuery(hfAccessQuery);
  const store = useStoreHfToken();
  const forget = useForgetHfToken();
  const [token, setToken] = useState("");

  if (access.data?.present) {
    return (
      <div className="cf-store__hf">
        <span className="cf-store__jobtext">
          Hugging Face token stored for {access.data.handle} — gated downloads can authenticate.
        </span>
        <button
          type="button"
          className="cf-button cf-button--ghost cf-button--sm"
          disabled={forget.isPending}
          onClick={() => forget.mutate()}
        >
          Forget it
        </button>
      </div>
    );
  }
  return (
    <form
      className="cf-store__hf"
      onSubmit={(event) => {
        event.preventDefault();
        if (token.trim().length < 8) return;
        store.mutate(token.trim(), { onSuccess: () => setToken("") });
      }}
    >
      <label className="cf-store__jobtext" htmlFor="cf-hf-token">
        Hugging Face token (for gated weights: LTX-2.5, Stable Audio, SAM 3.1)
      </label>
      <input
        id="cf-hf-token"
        type="password"
        className="cf-input"
        autoComplete="off"
        placeholder="hf_…"
        value={token}
        onChange={(event) => setToken(event.target.value)}
      />
      <button
        type="submit"
        className="cf-button cf-button--secondary cf-button--sm"
        disabled={token.trim().length < 8 || store.isPending}
      >
        Save token
      </button>
      {store.isError && <span className="cf-store__jobtext--bad">{String(store.error)}</span>}
    </form>
  );
}

export function StoreBar({ catalog }: { catalog: ModelCatalog }) {
  const relink = useRelinkModels();
  return (
    <div className="cf-store__bar">
      <dl className="cf-store__barfacts">
        <dt>Weight store</dt>
        <dd>
          <code>{catalog.store}</code>
          {!catalog.store_exists && <span className="cf-store__jobtext--bad"> (missing)</span>}
        </dd>
        <dt>Free space</dt>
        <dd>
          {formatBytes(catalog.store_free_bytes)} of {formatBytes(catalog.store_total_bytes)}
        </dd>
        <dt>Index</dt>
        <dd>
          <code>{catalog.index_root}</code>
        </dd>
        <dt>ComfyUI models</dt>
        <dd>{catalog.comfy_models_dir ? <code>{catalog.comfy_models_dir}</code> : "not configured"}</dd>
      </dl>
      <div className="cf-store__baractions">
        <button
          type="button"
          className="cf-button cf-button--secondary cf-button--sm"
          disabled={relink.isPending}
          onClick={() => relink.mutate()}
        >
          {relink.isPending ? "Linking…" : "Repair all links"}
        </button>
        {relink.data && (
          <span className="cf-store__jobtext" role="status">
            {relink.data.index.length} index links, {relink.data.comfy.length} ComfyUI links
          </span>
        )}
        <HuggingFaceAccessRow />
      </div>
    </div>
  );
}
