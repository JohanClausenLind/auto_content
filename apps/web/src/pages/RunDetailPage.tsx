import { deliverableOf, formatDuration, RunCanvas, RunNodeList, type RunNode } from "@content-factory/pipeline-canvas";
import { Button, Dialog, TextField } from "@content-factory/web-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";
import { api, isApiError } from "../api/client";
import { queryKeys, runQuery } from "../api/queries";
import type { ApprovalDecision, ApprovalRequest, RunDetail } from "../api/types";
import { ErrorState, LoadingState, Page } from "./EmptyState";
import { RevisionBox } from "./RevisionBox";
import { formatWhen, runStateExplanation, runStateLabel } from "./runState";

export function RunDetailPage() {
  const { runId } = useParams({ strict: false });
  if (!runId) return null;
  return <RunDetailInner runId={runId} />;
}

function RunDetailInner({ runId }: { runId: string }) {
  const run = useQuery(runQuery(runId));
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (run.isPending) {
    return (
      <Page title="Run">
        <LoadingState label="Loading run…" />
      </Page>
    );
  }
  if (run.isError) {
    return (
      <Page title="Run">
        <ErrorState title="Couldn't load this run" {...(isApiError(run.error) ? { detail: run.error.detail } : {})} retry={() => void run.refetch()} />
      </Page>
    );
  }

  const detail = run.data;
  const selected = detail.nodes.find((n) => n.node_id === selectedId) ?? null;
  const onSelectNode = (node: RunNode) => setSelectedId((current) => (current === node.node_id ? null : node.node_id));

  return (
    <article className="cf-page cf-rundetail">
      <header className="cf-page__header">
        <p className="cf-rundetail__breadcrumb">
          <Link to="/projects">Projects</Link> / <span>{detail.run_id}</span>
        </p>
        <div className="cf-rundetail__titlerow">
          <h1 className="cf-page__title">Run {detail.run_id}</h1>
          <span className="cf-runstate" data-state={detail.state}>
            {runStateLabel(detail.state)}
          </span>
        </div>
        <p className="cf-page__lead">{runStateExplanation(detail.state)}</p>
        <p className="cf-rundetail__meta">
          {detail.quality} quality · campaign {detail.campaign_id} · project {detail.project_id} · started {formatWhen(detail.created_at)}
          {detail.approved_by ? ` · approved by ${detail.approved_by}` : ""}
        </p>
      </header>

      {detail.error && (
        <p role="alert" className="cf-rundetail__error">
          {detail.error}
        </p>
      )}

      {detail.state === "WAITING_FOR_APPROVAL" && detail.preflight_revision_hash && (
        <ApprovalBanner runId={detail.run_id} revisionHash={detail.preflight_revision_hash} />
      )}

      <section className="cf-rundag" aria-label="Pipeline">
        <div className="cf-rundag__main">
          <RunCanvas nodes={detail.nodes} onSelect={onSelectNode} selectedNodeId={selectedId} aria-label="Pipeline graph" />
          <h2 className="cf-rundag__heading">Steps</h2>
          <RunNodeList nodes={detail.nodes} onSelect={onSelectNode} selectedNodeId={selectedId} />
        </div>
        {selected && <NodeInspector node={selected} onClose={() => setSelectedId(null)} />}
      </section>

      {detail.report != null && <RunReport report={detail.report} />}

      <RevisionBox projectId={detail.project_id} />
    </article>
  );
}

function NodeInspector({ node, onClose }: { node: RunNode; onClose: () => void }) {
  const duration = formatDuration(node.duration_ms);
  return (
    <aside className="cf-inspector" aria-label="Node details">
      <header className="cf-inspector__header">
        <h2 className="cf-inspector__title">{node.stage}</h2>
        <Button variant="ghost" size="sm" aria-label="Close details" onPress={onClose}>
          <span aria-hidden="true">×</span>
        </Button>
      </header>
      <dl className="cf-inspector__facts">
        <dt>Stage</dt>
        <dd>{node.stage}</dd>
        <dt>Deliverable</dt>
        <dd>{deliverableOf(node) ?? "Shared across deliverables"}</dd>
        <dt>State</dt>
        <dd>
          <span className="cf-runstate" data-state={node.state}>
            {node.state}
          </span>
        </dd>
        <dt>Attempts</dt>
        <dd>{node.attempts}</dd>
        <dt>Duration</dt>
        <dd>{duration || "—"}</dd>
        <dt>Cache</dt>
        <dd>{node.cache_hit ? "Served from cache" : "Computed fresh"}</dd>
        {node.error && (
          <>
            <dt>Error</dt>
            <dd className="cf-inspector__error">{node.error}</dd>
          </>
        )}
      </dl>
    </aside>
  );
}

function ApprovalBanner({ runId, revisionHash }: { runId: string; revisionHash: string }) {
  const client = useQueryClient();
  const [reason, setReason] = useState("");
  const [stepUpOpen, setStepUpOpen] = useState(false);
  const [pending, setPending] = useState<ApprovalRequest | null>(null);

  const approval = useMutation({
    mutationFn: (body: ApprovalRequest) => api.runs.approval(runId, body),
    onSuccess: () => {
      setPending(null);
      void client.invalidateQueries({ queryKey: queryKeys.run(runId) });
      void client.invalidateQueries({ queryKey: queryKeys.runs });
    },
    onError: (error, body) => {
      if (isApiError(error) && error.stepUpRequired) {
        setPending(body);
        setStepUpOpen(true);
      }
    },
  });

  const decide = (decision: ApprovalDecision) => {
    const trimmed = reason.trim();
    approval.mutate({ revision_hash: revisionHash, decision, ...(trimmed ? { reason: trimmed } : {}) });
  };

  const showError = approval.error && !(isApiError(approval.error) && approval.error.stepUpRequired);

  return (
    <section className="cf-approval" aria-labelledby="cf-approval-title">
      <div className="cf-approval__text">
        <h2 id="cf-approval-title" className="cf-approval__title">
          Approval needed
        </h2>
        <p className="cf-approval__body">Review the preflight result, then approve to start production. Nothing is made until you do.</p>
        <p className="cf-approval__hash">
          Plan revision <code>{revisionHash}</code>
        </p>
      </div>
      <div className="cf-approval__controls">
        <TextField label="Note (required only if you reject)" name="approval-reason" value={reason} onChange={setReason} className="cf-approval__reason" />
        <div className="cf-approval__buttons">
          <Button variant="primary" onPress={() => decide("approve")} isDisabled={approval.isPending} isPending={approval.isPending}>
            Approve
          </Button>
          <Button variant="danger" onPress={() => decide("reject")} isDisabled={approval.isPending}>
            Reject
          </Button>
        </div>
      </div>
      {showError && (
        <p role="alert" className="cf-approval__error">
          {isApiError(approval.error) ? approval.error.detail : "The decision didn't go through. Try again."}
        </p>
      )}
      <StepUpDialog
        isOpen={stepUpOpen}
        onOpenChange={(open) => {
          setStepUpOpen(open);
          if (!open) setPending(null);
        }}
        onVerified={() => {
          setStepUpOpen(false);
          if (pending) approval.mutate(pending);
        }}
      />
    </section>
  );
}

function StepUpDialog({ isOpen, onOpenChange, onVerified }: { isOpen: boolean; onOpenChange: (open: boolean) => void; onVerified: () => void }) {
  const [password, setPassword] = useState("");
  const stepUp = useMutation({
    mutationFn: api.session.stepUp,
    onSuccess: () => {
      setPassword("");
      onVerified();
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (password) stepUp.mutate(password);
  };

  return (
    <Dialog title="Confirm it's you" description="Approvals need a fresh sign-in. Enter your password to continue." size="sm" isOpen={isOpen} onOpenChange={onOpenChange}>
      <form className="cf-stepup" onSubmit={submit} noValidate>
        <TextField label="Password" name="password" type="password" value={password} onChange={setPassword} autoComplete="current-password" isRequired autoFocus />
        {stepUp.error && (
          <p role="alert" className="cf-stepup__error">
            {isApiError(stepUp.error) ? stepUp.error.detail : "That didn't work. Try again."}
          </p>
        )}
        <Button type="submit" variant="primary" isDisabled={!password || stepUp.isPending} isPending={stepUp.isPending}>
          {stepUp.isPending ? "Checking…" : "Confirm"}
        </Button>
      </form>
    </Dialog>
  );
}

function RunReport({ report }: { report: RunDetail["report"] }) {
  return (
    <details className="cf-rundetail__report">
      <summary>Run report</summary>
      <pre>{JSON.stringify(report, null, 2)}</pre>
    </details>
  );
}
