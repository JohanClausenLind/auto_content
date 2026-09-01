import { Button } from "@content-factory/web-ui";
import { useMutation } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useId, useState, type FormEvent } from "react";
import { api, isApiError } from "../api/client";
import type { RevisionOutcome } from "../api/types";

export interface RevisionBoxProps {
  projectId: string;
}

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

function formatSeconds(seconds: number): string {
  if (seconds < 60) return `about ${Math.max(1, Math.round(seconds))} seconds`;
  const minutes = Math.round(seconds / 60);
  return `about ${minutes} minute${minutes === 1 ? "" : "s"}`;
}

function OutcomeView({ outcome, onConfirm, applying }: { outcome: RevisionOutcome; onConfirm: () => void; applying: boolean }) {
  switch (outcome.kind) {
    case "fix_plan": {
      const units = outcome.impact.affected_unit_ids;
      return (
        <div className="cf-outcome cf-outcome--plan">
          <h3 className="cf-outcome__title">Here's the plan</h3>
          <p className="cf-outcome__body">{outcome.plain_language}</p>
          <p className="cf-outcome__estimates">
            Estimated cost {formatUsd(outcome.estimated_cost_usd)} · {formatSeconds(outcome.estimated_seconds)}
          </p>
          {units.length > 0 && (
            <>
              <p className="cf-outcome__hint">
                Affects {units.length} unit{units.length === 1 ? "" : "s"}:
              </p>
              <ul className="cf-outcome__units">
                {units.map((id) => (
                  <li key={id}>
                    <code>{id}</code>
                  </li>
                ))}
              </ul>
            </>
          )}
          <Button variant="primary" onPress={onConfirm} isDisabled={applying} isPending={applying}>
            {applying ? "Starting rebuild…" : "Confirm and rebuild"}
          </Button>
        </div>
      );
    }
    case "clarifying_question":
      return (
        <div className="cf-outcome cf-outcome--question">
          <h3 className="cf-outcome__title">One question first</h3>
          <p className="cf-outcome__body">{outcome.question}</p>
          {outcome.candidate_unit_ids.length > 0 && (
            <p className="cf-outcome__hint">It could be about: {outcome.candidate_unit_ids.join(", ")}</p>
          )}
          <p className="cf-outcome__hint">Add a bit more detail above and send it again.</p>
        </div>
      );
    case "refusal":
      return (
        <div className="cf-outcome cf-outcome--calm">
          <h3 className="cf-outcome__title">That change can't be made</h3>
          <p className="cf-outcome__body">{outcome.reason}</p>
          <p className="cf-outcome__hint">Policy: {outcome.policy}</p>
          <p className="cf-outcome__hint">You can rephrase what you'd like changed — content-level tweaks usually go through.</p>
        </div>
      );
    case "gate_required":
      return (
        <div className="cf-outcome cf-outcome--calm">
          <h3 className="cf-outcome__title">This needs a gate first</h3>
          <p className="cf-outcome__body">{outcome.reason}</p>
          <p className="cf-outcome__hint">Required gate: {outcome.gate}. Nothing was changed.</p>
          <p className="cf-outcome__hint">You can adjust the request above, or ask an owner to open the gate.</p>
        </div>
      );
  }
}

/**
 * Persistent free-text feedback box. Submitting proposes a revision and renders
 * the typed outcome; confirming a fix plan starts a targeted rebuild run.
 */
export function RevisionBox({ projectId }: RevisionBoxProps) {
  const inputId = useId();
  const [feedback, setFeedback] = useState("");
  const [outcome, setOutcome] = useState<RevisionOutcome | null>(null);
  const [proposedText, setProposedText] = useState("");
  const [startedRunId, setStartedRunId] = useState<string | null>(null);

  const propose = useMutation({
    mutationFn: (text: string) => api.revisions.propose({ project_id: projectId, feedback: text }),
    onSuccess: (result, text) => {
      setOutcome(result);
      setProposedText(text);
    },
  });

  const apply = useMutation({
    mutationFn: (text: string) => api.revisions.apply({ project_id: projectId, feedback: text }),
    onSuccess: ({ run_id }) => {
      setStartedRunId(run_id);
      setOutcome(null);
      setFeedback("");
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const text = feedback.trim();
    if (!text) return;
    setStartedRunId(null);
    setOutcome(null);
    propose.mutate(text);
  };

  const error = propose.error ?? apply.error;

  return (
    <section className="cf-revision" aria-labelledby={`${inputId}-title`}>
      <h2 id={`${inputId}-title`} className="cf-revision__heading">
        Want something changed?
      </h2>
      <form className="cf-revision__form" onSubmit={submit}>
        <label className="cf-revision__label" htmlFor={inputId}>
          Tell me what you don't like.
        </label>
        <textarea
          id={inputId}
          className="cf-input cf-revision__input"
          rows={3}
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          placeholder="e.g. The intro is too long and the thumbnail text is hard to read."
        />
        <div className="cf-revision__actions">
          <Button type="submit" variant="primary" isDisabled={feedback.trim() === "" || propose.isPending} isPending={propose.isPending}>
            {propose.isPending ? "Thinking…" : "Send feedback"}
          </Button>
        </div>
      </form>

      {error && (
        <p role="alert" className="cf-revision__error">
          {isApiError(error) ? error.detail : "That didn't go through. Try again."}
        </p>
      )}

      {outcome && <OutcomeView outcome={outcome} onConfirm={() => apply.mutate(proposedText)} applying={apply.isPending} />}

      {startedRunId && (
        <div className="cf-toast" role="status">
          <span>Rebuild started.</span>
          <Link className="cf-toast__link" to="/projects/$runId" params={{ runId: startedRunId }}>
            View run
          </Link>
          <Button variant="ghost" size="sm" aria-label="Dismiss" onPress={() => setStartedRunId(null)}>
            <span aria-hidden="true">×</span>
          </Button>
        </div>
      )}
    </section>
  );
}
