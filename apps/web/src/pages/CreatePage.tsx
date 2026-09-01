import { Button, TextField } from "@content-factory/web-ui";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from "react";
import { api, isApiError } from "../api/client";
import { matrixQuery } from "../api/queries";
import type { CampaignBody, CampaignPreview, DagNode, MatrixRow, PrunedStage, RunQuality } from "../api/types";
import { ErrorState, LoadingState, Page } from "./EmptyState";

/** Plain-language names for the wire-format deliverable types. */
const TYPE_LABEL: Record<string, string> = {
  long_video: "Long video",
  short_video: "Short video",
  single_image_post: "Single image post",
  carousel: "Carousel",
  infographic: "Infographic",
  text_post: "Text post",
  thread: "Thread",
  article: "Article",
  newsletter: "Newsletter",
  email_campaign: "Email campaign",
  audio_clip: "Audio clip",
  audiogram: "Audiogram",
  cover: "Cover image",
  image_sequence: "Image sequence",
};

export function typeLabel(type: string): string {
  return TYPE_LABEL[type] ?? type.replace(/_/g, " ");
}

function stageLabel(stage: string): string {
  return stage.replace(/_/g, " ");
}

export function formatEstimates(e: CampaignPreview["estimates"]): string {
  const cost = `$${e.external_cost_usd.toFixed(2)} external`;
  if (e.external_calls === 0 && e.local_render) return `${cost} — everything renders locally`;
  const calls = `${e.external_calls} external call${e.external_calls === 1 ? "" : "s"}`;
  return `${cost} across ${calls}${e.local_render ? " — rendering stays local" : ""}`;
}

interface Pick {
  title: string;
  cardCount: number;
}

const STEPS = ["What it's about", "What to make", "Review the plan"] as const;
type StepIndex = 1 | 2 | 3;

export function CreatePage() {
  const navigate = useNavigate();
  const [step, setStep] = useState<StepIndex>(1);
  const [topic, setTopic] = useState("");
  const [objective, setObjective] = useState("");
  const [quality, setQuality] = useState<RunQuality>("demo");
  const [picks, setPicks] = useState<Record<string, Pick>>({});
  /** Deliverable type a 422 pointed at; highlighted on step 2. */
  const [offendingType, setOffendingType] = useState<string | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const mounted = useRef(false);

  // Keyboard-first: moving between steps puts focus on the step heading.
  useEffect(() => {
    if (mounted.current) headingRef.current?.focus();
    mounted.current = true;
  }, [step]);

  const matrix = useQuery({ ...matrixQuery, enabled: step === 2 || step === 3 });

  const body = useMemo<CampaignBody>(
    () => ({
      topic: topic.trim(),
      objective: objective.trim(),
      deliverables: (matrix.data?.rows ?? [])
        .filter((row) => row.type in picks)
        .map((row) => {
          const pick = picks[row.type];
          const title = pick?.title.trim() || topic.trim();
          return { type: row.type, title, ...(row.type === "carousel" ? { card_count: pick?.cardCount ?? 5 } : {}) };
        }),
      quality,
    }),
    [topic, objective, quality, picks, matrix.data],
  );

  const rememberOffender = (error: unknown) => {
    if (isApiError(error) && error.status === 422) {
      const type = error.detail.split(":")[0]?.trim() ?? null;
      setOffendingType(type && type in picks ? type : null);
    }
  };

  const create = useMutation({
    mutationFn: api.campaigns.create,
    onSuccess: ({ run_id }) => void navigate({ to: "/projects/$runId", params: { runId: run_id } }),
    onError: rememberOffender,
  });

  const toggle = (row: MatrixRow, checked: boolean) => {
    setOffendingType(null);
    setPicks((current) => {
      const next = { ...current };
      if (checked) next[row.type] = current[row.type] ?? { title: topic.trim(), cardCount: 5 };
      else delete next[row.type];
      return next;
    });
  };

  const setPick = (type: string, patch: Partial<Pick>) => {
    setPicks((current) => {
      const existing = current[type];
      if (!existing) return current;
      return { ...current, [type]: { ...existing, ...patch } };
    });
  };

  const selectedCount = Object.keys(picks).length;

  return (
    <Page title="Create" lead="Three steps: describe it, pick the formats, review the plan. Nothing runs until the end.">
      <ol className="cf-steps" aria-label="Steps">
        {STEPS.map((label, i) => {
          const n = (i + 1) as StepIndex;
          return (
            <li key={label} className="cf-steps__item" data-state={n === step ? "current" : n < step ? "done" : "todo"} {...(n === step ? { "aria-current": "step" as const } : {})}>
              <span className="cf-steps__number" aria-hidden="true">
                {n}
              </span>
              {label}
            </li>
          );
        })}
      </ol>

      {step === 1 && (
        <StepOne
          headingRef={headingRef}
          topic={topic}
          objective={objective}
          quality={quality}
          onTopic={setTopic}
          onObjective={setObjective}
          onQuality={setQuality}
          onContinue={() => setStep(2)}
        />
      )}

      {step === 2 && (
        <section className="cf-create__step" aria-labelledby="cf-create-step2">
          <h2 id="cf-create-step2" className="cf-create__heading" tabIndex={-1} ref={headingRef}>
            What should the factory make?
          </h2>
          {matrix.isPending ? (
            <LoadingState label="Loading what this build can make…" />
          ) : matrix.isError ? (
            <ErrorState title="Couldn't load the format list" {...(isApiError(matrix.error) ? { detail: matrix.error.detail } : {})} retry={() => void matrix.refetch()} />
          ) : (
            <>
              <MatrixTable rows={matrix.data.rows} picks={picks} topic={topic} offendingType={offendingType} onToggle={toggle} onPick={setPick} />
              {matrix.data.notes.length > 0 && (
                <ul className="cf-create__notes">
                  {matrix.data.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              )}
            </>
          )}
          <div className="cf-create__nav">
            <Button variant="secondary" onPress={() => setStep(1)}>
              Back
            </Button>
            <Button variant="primary" onPress={() => setStep(3)} isDisabled={selectedCount === 0}>
              {selectedCount === 0 ? "Pick at least one format" : `Continue with ${selectedCount} format${selectedCount === 1 ? "" : "s"}`}
            </Button>
          </div>
        </section>
      )}

      {step === 3 && (
        <StepThree
          headingRef={headingRef}
          body={body}
          onBack={() => setStep(2)}
          onCreate={() => create.mutate(body)}
          creating={create.isPending}
          createError={create.error}
          onOffense={(error) => {
            rememberOffender(error);
          }}
          goFix={() => setStep(2)}
        />
      )}
    </Page>
  );
}

function StepOne({
  headingRef,
  topic,
  objective,
  quality,
  onTopic,
  onObjective,
  onQuality,
  onContinue,
}: {
  headingRef: React.RefObject<HTMLHeadingElement | null>;
  topic: string;
  objective: string;
  quality: RunQuality;
  onTopic: (v: string) => void;
  onObjective: (v: string) => void;
  onQuality: (v: RunQuality) => void;
  onContinue: () => void;
}) {
  const objectiveId = useId();
  const qualityId = useId();
  const ready = topic.trim().length > 0 && objective.trim().length > 0;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (ready) onContinue();
  };
  return (
    <section className="cf-create__step" aria-labelledby="cf-create-step1">
      <h2 id="cf-create-step1" className="cf-create__heading" tabIndex={-1} ref={headingRef}>
        What is this campaign about?
      </h2>
      <form className="cf-create__form" onSubmit={submit} noValidate>
        <TextField label="Topic" name="topic" value={topic} onChange={onTopic} description="One line about the subject. Research uses offline fixtures in this build." isRequired autoFocus />
        <div className="cf-field">
          <label className="cf-field__label" htmlFor={objectiveId}>
            What should it achieve?
          </label>
          <textarea id={objectiveId} className="cf-input cf-create__objective" value={objective} onChange={(e) => onObjective(e.target.value)} rows={3} required />
          <p className="cf-field__description">Plain words are fine — “explain the feature to newcomers”, “announce the release”.</p>
        </div>
        <div className="cf-field cf-create__quality">
          <label className="cf-field__label" htmlFor={qualityId}>
            Quality
          </label>
          <select id={qualityId} className="cf-input" value={quality} onChange={(e) => onQuality(e.target.value as RunQuality)}>
            <option value="demo">demo — full fixture quality</option>
            <option value="smoke">smoke — fastest, for a quick check</option>
          </select>
        </div>
        <div className="cf-create__nav">
          <Button type="submit" variant="primary" isDisabled={!ready}>
            Continue
          </Button>
        </div>
      </form>
    </section>
  );
}

function MatrixTable({
  rows,
  picks,
  topic,
  offendingType,
  onToggle,
  onPick,
}: {
  rows: MatrixRow[];
  picks: Record<string, Pick>;
  topic: string;
  offendingType: string | null;
  onToggle: (row: MatrixRow, checked: boolean) => void;
  onPick: (type: string, patch: Partial<Pick>) => void;
}) {
  return (
    <div className="cf-runs__tablewrap">
      <table className="cf-runs cf-matrix">
        <caption className="cf-visually-hidden">Content formats this build can make</caption>
        <thead>
          <tr>
            <th scope="col">Make it</th>
            <th scope="col">Format</th>
            <th scope="col">Details</th>
            <th scope="col">Destinations</th>
            <th scope="col">Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const pick = picks[row.type];
            const selected = pick !== undefined;
            const offending = offendingType === row.type;
            return (
              <tr key={row.type} className={["cf-matrix__row", row.supported ? "" : "cf-matrix__row--disabled", offending ? "cf-matrix__row--invalid" : ""].join(" ").trim()}>
                <td className="cf-matrix__check">
                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={!row.supported}
                    aria-label={typeLabel(row.type)}
                    onChange={(e) => onToggle(row, e.target.checked)}
                  />
                </td>
                <th scope="row" className="cf-matrix__type">
                  {typeLabel(row.type)}
                </th>
                <td className="cf-matrix__details">
                  {selected && (
                    <div className="cf-matrix__fields">
                      <TextField label={`Title for ${typeLabel(row.type).toLowerCase()}`} value={pick.title} onChange={(v) => onPick(row.type, { title: v })} placeholder={topic.trim() || "Title"} />
                      {row.type === "carousel" && (
                        <label className="cf-matrix__count">
                          <span className="cf-field__label">Cards</span>
                          <input
                            type="number"
                            className="cf-input"
                            min={2}
                            max={20}
                            value={pick.cardCount}
                            onChange={(e) => {
                              const n = Number(e.target.value);
                              if (Number.isInteger(n)) onPick(row.type, { cardCount: n });
                            }}
                          />
                        </label>
                      )}
                    </div>
                  )}
                  {!row.supported && <span className="cf-matrix__unavailable">Not in this build</span>}
                </td>
                <td className="cf-matrix__destinations">export (package only)</td>
                <td className="cf-matrix__reason">
                  {row.reason}
                  {offending && (
                    <p role="alert" className="cf-matrix__alert">
                      The preview refused this selection — change or remove it.
                    </p>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StepThree({
  headingRef,
  body,
  onBack,
  onCreate,
  creating,
  createError,
  onOffense,
  goFix,
}: {
  headingRef: React.RefObject<HTMLHeadingElement | null>;
  body: CampaignBody;
  onBack: () => void;
  onCreate: () => void;
  creating: boolean;
  createError: unknown;
  onOffense: (error: unknown) => void;
  goFix: () => void;
}) {
  const preview = useQuery({
    queryKey: ["campaigns", "preview", body] as const,
    queryFn: () => api.campaigns.preview(body),
  });

  // A 422 from preview points back at a selection on step 2.
  const previewRejected = preview.isError && isApiError(preview.error) && preview.error.status === 422;
  useEffect(() => {
    if (previewRejected) onOffense(preview.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewRejected]);

  return (
    <section className="cf-create__step" aria-labelledby="cf-create-step3">
      <h2 id="cf-create-step3" className="cf-create__heading" tabIndex={-1} ref={headingRef}>
        Review the plan
      </h2>
      {preview.isPending ? (
        <LoadingState label="Compiling the pipeline…" />
      ) : preview.isError ? (
        previewRejected ? (
          <div role="alert" className="cf-create__rejected">
            <h3 className="cf-create__rejectedtitle">One selection can't run in this build</h3>
            <p>{isApiError(preview.error) ? preview.error.detail : ""}</p>
            <Button variant="secondary" onPress={goFix}>
              Back to fix the selection
            </Button>
          </div>
        ) : (
          <ErrorState title="Couldn't compile the preview" {...(isApiError(preview.error) ? { detail: preview.error.detail } : {})} retry={() => void preview.refetch()} />
        )
      ) : (
        <PreviewView data={preview.data} />
      )}

      {createError != null && !(isApiError(createError) && createError.status === 422) && (
        <p role="alert" className="cf-create__error">
          {isApiError(createError) ? createError.detail : "Couldn't create the campaign. Try again."}
        </p>
      )}
      {isApiError(createError) && createError.status === 422 && (
        <div role="alert" className="cf-create__rejected">
          <p>{createError.detail}</p>
          <Button variant="secondary" onPress={goFix}>
            Back to fix the selection
          </Button>
        </div>
      )}

      <div className="cf-create__nav">
        <Button variant="secondary" onPress={onBack}>
          Back
        </Button>
        <Button variant="secondary" onPress={onCreate} isDisabled={creating || !preview.isSuccess}>
          Run preflight only
        </Button>
        <Button variant="primary" onPress={onCreate} isDisabled={creating || !preview.isSuccess} isPending={creating}>
          {creating ? "Creating…" : "Create & run"}
        </Button>
      </div>
      <p className="cf-create__hint">Either way, nothing is produced until you approve the preflight plan on the run page.</p>
    </section>
  );
}

function PreviewView({ data }: { data: CampaignPreview }) {
  const titles = new Map(data.campaign.deliverables.map((d) => [d.deliverable_id, `${typeLabel(d.type)} — ${d.title}`]));
  const shared = data.dag.nodes.filter((n) => n.deliverable_id === null);
  const grouped = new Map<string, DagNode[]>();
  for (const node of data.dag.nodes) {
    if (node.deliverable_id === null) continue;
    const list = grouped.get(node.deliverable_id) ?? [];
    list.push(node);
    grouped.set(node.deliverable_id, list);
  }
  return (
    <div className="cf-preview">
      <p className="cf-preview__estimates">{formatEstimates(data.estimates)}</p>

      <section className="cf-preview__group" aria-labelledby="cf-preview-shared">
        <h3 id="cf-preview-shared" className="cf-preview__heading">
          Shared stages
        </h3>
        <StageList nodes={shared} />
      </section>

      {[...grouped.entries()].map(([id, nodes]) => (
        <section key={id} className="cf-preview__group" aria-label={titles.get(id) ?? id}>
          <h3 className="cf-preview__heading">{titles.get(id) ?? id}</h3>
          <StageList nodes={nodes} />
        </section>
      ))}

      {data.dag.pruned.length > 0 && (
        <section className="cf-preview__group cf-preview__group--pruned" aria-labelledby="cf-preview-pruned">
          <h3 id="cf-preview-pruned" className="cf-preview__heading">
            Skipped (with reasons)
          </h3>
          <ul className="cf-preview__list">
            {data.dag.pruned.map((p: PrunedStage) => (
              <li key={`${p.stage}:${p.deliverable_id ?? "shared"}`}>
                <span className="cf-preview__stage">{stageLabel(p.stage)}</span>
                {p.deliverable_id !== null && <span className="cf-preview__for"> for {titles.get(p.deliverable_id) ?? p.deliverable_id}</span>} — {p.reason}
              </li>
            ))}
          </ul>
        </section>
      )}

      {data.notes.length > 0 && (
        <ul className="cf-create__notes">
          {data.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StageList({ nodes }: { nodes: DagNode[] }) {
  if (nodes.length === 0) return <p className="cf-preview__none">None.</p>;
  return (
    <ol className="cf-preview__list">
      {nodes.map((n) => (
        <li key={n.node_id}>
          <span className="cf-preview__stage">{stageLabel(n.stage)}</span>
          {n.depends_on.length > 0 && <span className="cf-preview__deps"> · after {n.depends_on.map(stageLabelFromNodeId).join(", ")}</span>}
        </li>
      ))}
    </ol>
  );
}

function stageLabelFromNodeId(nodeId: string): string {
  return stageLabel(nodeId.split(":")[0] ?? nodeId);
}
