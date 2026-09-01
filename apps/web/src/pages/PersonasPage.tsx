import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api, isApiError } from "../api/client";
import { personaQuery, personasQuery, queryKeys } from "../api/queries";
import type { PersonaDiff } from "../api/types";
import { EmptyState, ErrorState, LoadingState, Page } from "./EmptyState";

/** Personas are governed assets: revise previews a typed diff, apply is revision-bound. */
export function PersonasPage() {
  const list = useQuery(personasQuery);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  if (list.isPending) return wrap(<LoadingState label="Loading personas…" />);
  if (list.isError)
    return wrap(<ErrorState {...(isApiError(list.error) ? { detail: list.error.detail } : {})} retry={() => void list.refetch()} />);

  const personas = list.data.filter((p) => !p.archived);
  return wrap(
    <>
      {personas.length === 0 && !creating ? (
        <EmptyState
          title="No personas yet"
          body="A persona holds tone, boundaries and disclosure rules. Every reply and script is written through one."
          action={
            <button type="button" className="cf-button cf-button--primary cf-button--md" onClick={() => setCreating(true)}>
              New persona
            </button>
          }
        />
      ) : (
        <div className="cf-personas">
          <div className="cf-personas__toolbar">
            <button type="button" className="cf-button cf-button--secondary cf-button--sm" onClick={() => setCreating((c) => !c)}>
              {creating ? "Cancel" : "New persona"}
            </button>
          </div>
          {creating && <CreatePersonaForm onDone={(id) => (setCreating(false), setSelected(id))} />}
          <ul className="cf-personas__list">
            {personas.map((p) => (
              <li key={p.id} className="cf-personas__item">
                <button
                  type="button"
                  className="cf-personas__open"
                  aria-expanded={selected === p.id}
                  onClick={() => setSelected(selected === p.id ? null : p.id)}
                >
                  <span className="cf-personas__name">{p.name}</span>
                  <span className="cf-personas__rev">revision {p.revision}</span>
                </button>
                {selected === p.id && <PersonaDetailCard personaId={p.id} />}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>,
  );
}

function wrap(children: React.ReactNode) {
  return (
    <Page title="Personas" lead="The voices your content is written in. Safety rules are code and cannot be edited here.">
      {children}
    </Page>
  );
}

function CreatePersonaForm({ onDone }: { onDone: (id: string) => void }) {
  const client = useQueryClient();
  const nameId = useId();
  const ageId = useId();
  const toneId = useId();
  const bioId = useId();
  const [name, setName] = useState("");
  const [age, setAge] = useState("25");
  const [tone, setTone] = useState("warm");
  const [bio, setBio] = useState("");
  const create = useMutation({
    mutationFn: () =>
      api.personas.create({
        identity: { display_name: name.trim(), presented_age: Number(age) },
        voice: { tone: tone.split(",").map((t) => t.trim()).filter(Boolean) },
        backstory: { bio: bio.trim() },
      }),
    onSuccess: (p) => {
      void client.invalidateQueries({ queryKey: queryKeys.personas });
      onDone(p.id);
    },
  });
  return (
    <form
      className="cf-personas__create"
      onSubmit={(e) => {
        e.preventDefault();
        if (!create.isPending) create.mutate();
      }}
    >
      <div className="cf-field">
        <label className="cf-field__label" htmlFor={nameId}>
          Name
        </label>
        <input id={nameId} className="cf-input" value={name} onChange={(e) => setName(e.target.value)} required minLength={1} />
      </div>
      <div className="cf-field">
        <label className="cf-field__label" htmlFor={ageId}>
          Presented age
        </label>
        <input id={ageId} className="cf-input" type="number" min={18} max={120} value={age} onChange={(e) => setAge(e.target.value)} required />
        <p className="cf-field__description">Adults only — the system refuses anything under 18.</p>
      </div>
      <div className="cf-field">
        <label className="cf-field__label" htmlFor={toneId}>
          Tone words
        </label>
        <input id={toneId} className="cf-input" value={tone} onChange={(e) => setTone(e.target.value)} placeholder="warm, teasing" />
      </div>
      <div className="cf-field">
        <label className="cf-field__label" htmlFor={bioId}>
          Backstory
        </label>
        <textarea id={bioId} className="cf-input" rows={2} value={bio} onChange={(e) => setBio(e.target.value)} />
        <p className="cf-field__description">The only biography any model will ever see for this persona.</p>
      </div>
      {create.isError && <p className="cf-error__detail" role="alert">{isApiError(create.error) ? create.error.detail : "Could not create the persona."}</p>}
      <button type="submit" className="cf-button cf-button--primary cf-button--md" disabled={create.isPending}>
        {create.isPending ? "Creating…" : "Create persona"}
      </button>
    </form>
  );
}

function PersonaDetailCard({ personaId }: { personaId: string }) {
  const client = useQueryClient();
  const detail = useQuery(personaQuery(personaId));
  const feedbackId = useId();
  const [feedback, setFeedback] = useState("");
  const [diff, setDiff] = useState<PersonaDiff | null>(null);
  const revise = useMutation({
    mutationFn: () => api.personas.revise(personaId, feedback.trim()),
    onSuccess: setDiff,
  });
  const apply = useMutation({
    mutationFn: (d: PersonaDiff) => api.personas.apply(personaId, d),
    onSuccess: () => {
      setDiff(null);
      setFeedback("");
      void client.invalidateQueries({ queryKey: queryKeys.personas });
      void client.invalidateQueries({ queryKey: queryKeys.persona(personaId) });
    },
  });

  if (detail.isPending) return <LoadingState label="Loading persona…" />;
  if (detail.isError) return <ErrorState {...(isApiError(detail.error) ? { detail: detail.error.detail } : {})} retry={() => void detail.refetch()} />;
  const doc = detail.data.document;
  return (
    <section className="cf-persona-card" aria-label={`${detail.data.name} details`}>
      <dl className="cf-persona-card__facts">
        <dt>Tone</dt>
        <dd>{doc.voice.tone.join(", ")}</dd>
        <dt>Sentences</dt>
        <dd>{doc.voice.sentence_length}</dd>
        <dt>Emoji</dt>
        <dd>{doc.voice.emoji_policy}</dd>
        <dt>Disclosure</dt>
        <dd>{doc.disclosure === "disclose_on_ask" ? "answers honestly when asked" : "deflects, never claims human"}</dd>
      </dl>
      <form
        className="cf-persona-card__revise"
        onSubmit={(e) => {
          e.preventDefault();
          if (feedback.trim().length >= 3 && !revise.isPending) revise.mutate();
        }}
      >
        <div className="cf-field">
          <label className="cf-field__label" htmlFor={feedbackId}>
            Change something
          </label>
          <input
            id={feedbackId}
            className="cf-input"
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="e.g. too formal, keep it short, fewer emojis"
          />
          <p className="cf-field__description">You'll see the exact change before anything is applied.</p>
        </div>
        <button type="submit" className="cf-button cf-button--secondary cf-button--sm" disabled={revise.isPending || feedback.trim().length < 3}>
          {revise.isPending ? "Working…" : "Preview change"}
        </button>
        {revise.isError && <p className="cf-error__detail" role="alert">{isApiError(revise.error) ? revise.error.detail : "Could not map that feedback."}</p>}
      </form>
      {diff && (
        <div className="cf-persona-card__diff" role="region" aria-label="Proposed change">
          <table className="cf-runs">
            <caption className="cf-visually-hidden">Proposed persona changes</caption>
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Before</th>
                <th scope="col">After</th>
              </tr>
            </thead>
            <tbody>
              {diff.changes.map((c) => (
                <tr key={c.path}>
                  <th scope="row">{c.path}</th>
                  <td>{c.before}</td>
                  <td>{c.after}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="cf-persona-card__diffactions">
            <button type="button" className="cf-button cf-button--primary cf-button--sm" disabled={apply.isPending} onClick={() => apply.mutate(diff)}>
              {apply.isPending ? "Applying…" : `Apply (revision ${diff.base_revision + 1})`}
            </button>
            <button type="button" className="cf-button cf-button--ghost cf-button--sm" onClick={() => setDiff(null)}>
              Discard
            </button>
          </div>
          {apply.isError && (
            <p className="cf-error__detail" role="alert">
              {isApiError(apply.error) && apply.error.status === 409
                ? "The persona changed since this preview — ask for the change again."
                : "Could not apply the change."}
            </p>
          )}
        </div>
      )}
    </section>
  );
}
