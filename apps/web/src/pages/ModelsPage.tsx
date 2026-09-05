/**
 * Models: the store page. Everything the workflows need, installed from here.
 *
 * Missing comes first because that is what an operator opens this page for, and the button next
 * to a missing weight downloads it — to the configured weight store, then links it into the
 * repo's category index and ComfyUI's model folders, which is what makes it usable rather than
 * merely present. Installed sits below it, grouped the way the store is laid out, with the path
 * and licence of every family visible instead of implied.
 */

import { useMemo, useState } from "react";
import { isApiError } from "../api/client";
import type { ModelPackage } from "../api/types";
import {
  MissingModels,
  PackageRow,
  SkillEnvRow,
  StoreBar,
  formatBytes,
  useModelStore,
} from "../models/ModelStore";
import { ErrorState, LoadingState, Page } from "./EmptyState";

function matches(pkg: ModelPackage, needle: string): boolean {
  if (!needle) return true;
  return (
    pkg.name.toLowerCase().includes(needle) ||
    pkg.key.includes(needle) ||
    pkg.purpose.toLowerCase().includes(needle) ||
    pkg.labels.some((label) => label.toLowerCase().includes(needle))
  );
}

export function ModelsPage() {
  const store = useModelStore();
  const [query, setQuery] = useState("");
  const needle = query.trim().toLowerCase();
  const catalog = store.data;

  const installed = useMemo(
    () => (catalog?.packages ?? []).filter((p) => p.state === "ready" && matches(p, needle)),
    [catalog, needle],
  );
  const unwanted = useMemo(
    // Families no workflow currently declares: still installable, just not on anyone's critical
    // path, so they sit below the ones something is waiting for.
    () =>
      (catalog?.packages ?? []).filter(
        (p) => p.state !== "ready" && p.wanted_by.length === 0 && matches(p, needle),
      ),
    [catalog, needle],
  );
  const envs = catalog?.skill_envs ?? [];
  const onDisk = installed.reduce((sum, p) => sum + p.bytes_on_disk, 0);

  return (
    <Page
      title="Models"
      lead="Every weight and skill environment the workflows need. Install from here — files land in the weight store and are linked where ComfyUI and the skills look for them."
    >
      {store.isPending ? (
        <LoadingState label="Scanning the weight store…" />
      ) : store.isError ? (
        <ErrorState
          {...(isApiError(store.error) ? { detail: store.error.detail } : {})}
          retry={() => void store.refetch()}
        />
      ) : (
        <>
          <StoreBar catalog={store.data} />
          <MissingModels catalog={store.data} />

          <section className="cf-store__section" aria-label="Installed models">
            <header className="cf-store__sectionhead">
              <h2 className="cf-store__heading">
                Installed
                <span className="cf-store__count">{installed.length}</span>
              </h2>
              <input
                type="search"
                className="cf-input cf-store__search"
                placeholder="Search models…"
                aria-label="Search models"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <span className="cf-store__jobtext">{formatBytes(onDisk)} on disk</span>
            </header>
            <ul className="cf-store__list">
              {installed.map((pkg) => (
                <PackageRow key={pkg.key} pkg={pkg} jobs={store.data.jobs} />
              ))}
            </ul>
            {installed.length === 0 && <p className="cf-store__empty">Nothing installed matches.</p>}
          </section>

          {unwanted.length > 0 && (
            <section className="cf-store__section" aria-label="Available models">
              <header className="cf-store__sectionhead">
                <h2 className="cf-store__heading">
                  Also available
                  <span className="cf-store__count">{unwanted.length}</span>
                </h2>
                <span className="cf-store__jobtext">no current workflow declares these</span>
              </header>
              <ul className="cf-store__list">
                {unwanted.map((pkg) => (
                  <PackageRow key={pkg.key} pkg={pkg} jobs={store.data.jobs} />
                ))}
              </ul>
            </section>
          )}

          <section className="cf-store__section" aria-label="Skill environments">
            <header className="cf-store__sectionhead">
              <h2 className="cf-store__heading">
                Skill environments
                <span className="cf-store__count">{envs.filter((e) => e.state === "ready").length}/{envs.length}</span>
              </h2>
              <span className="cf-store__jobtext">isolated per skill; built with uv</span>
            </header>
            <ul className="cf-store__list">
              {envs.map((env) => (
                <SkillEnvRow key={env.key} env={env} jobs={store.data.jobs} />
              ))}
            </ul>
          </section>
        </>
      )}
    </Page>
  );
}
