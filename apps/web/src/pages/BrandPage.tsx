import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api, isApiError } from "../api/client";
import { brandEffectiveQuery, brandNodesQuery, queryKeys } from "../api/queries";
import type { BrandNode } from "../api/types";
import { EmptyState, ErrorState, LoadingState, Page } from "./EmptyState";

/** Brand hierarchy: locked keys inherit downward and cannot be overridden by children. */
export function BrandPage() {
  const nodes = useQuery(brandNodesQuery);
  const [adding, setAdding] = useState(false);
  const [inspecting, setInspecting] = useState<string | null>(null);

  if (nodes.isPending) return wrap(<LoadingState label="Loading brand hierarchy…" />);
  if (nodes.isError)
    return wrap(<ErrorState {...(isApiError(nodes.error) ? { detail: nodes.error.detail } : {})} retry={() => void nodes.refetch()} />);

  return wrap(
    nodes.data.length === 0 && !adding ? (
      <EmptyState
        title="No brand nodes yet"
        body="Start with a parent brand; child brands and locations inherit its locked colours, fonts and policies."
        action={
          <button type="button" className="cf-button cf-button--primary cf-button--md" onClick={() => setAdding(true)}>
            Create the parent brand
          </button>
        }
      />
    ) : (
      <div className="cf-brandtree">
        <div className="cf-personas__toolbar">
          <button type="button" className="cf-button cf-button--secondary cf-button--sm" onClick={() => setAdding((a) => !a)}>
            {adding ? "Cancel" : "Add brand node"}
          </button>
        </div>
        {adding && <AddNodeForm nodes={nodes.data} onDone={() => setAdding(false)} />}
        <ul className="cf-brandtree__list">
          {orderAsTree(nodes.data).map(({ node, depth }) => (
            <li key={node.id} className="cf-brandtree__item" style={{ paddingInlineStart: `${depth * 1.25}rem` }}>
              <button
                type="button"
                className="cf-personas__open"
                aria-expanded={inspecting === node.id}
                onClick={() => setInspecting(inspecting === node.id ? null : node.id)}
              >
                <span className="cf-personas__name">{node.name}</span>
                {(node.locked_tokens.length > 0 || node.locked_policies.length > 0) && (
                  <span className="cf-brandtree__lock">locks {[...node.locked_tokens, ...node.locked_policies].join(", ")}</span>
                )}
              </button>
              {inspecting === node.id && <EffectiveView nodeId={node.id} />}
            </li>
          ))}
        </ul>
      </div>
    ),
  );
}

function wrap(children: React.ReactNode) {
  return (
    <Page title="Brand" lead="Colours, fonts and policies for rendered content. Locked values flow down and can't be overridden.">
      {children}
    </Page>
  );
}

function orderAsTree(nodes: BrandNode[]): { node: BrandNode; depth: number }[] {
  const byParent = new Map<string | null, BrandNode[]>();
  for (const n of nodes) {
    const list = byParent.get(n.parent_id) ?? [];
    list.push(n);
    byParent.set(n.parent_id, list);
  }
  const out: { node: BrandNode; depth: number }[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const n of byParent.get(parent) ?? []) {
      out.push({ node: n, depth });
      walk(n.id, depth + 1);
    }
  };
  walk(null, 0);
  return out.length === nodes.length ? out : nodes.map((node) => ({ node, depth: 0 }));
}

function parsePairs(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const idx = line.indexOf("=");
    if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
  }
  return out;
}

function AddNodeForm({ nodes, onDone }: { nodes: BrandNode[]; onDone: () => void }) {
  const client = useQueryClient();
  const nameId = useId();
  const parentId = useId();
  const tokensId = useId();
  const locksId = useId();
  const [name, setName] = useState("");
  const [parent, setParent] = useState<string>("");
  const [tokens, setTokens] = useState("");
  const [locks, setLocks] = useState("");
  const create = useMutation({
    mutationFn: () =>
      api.brands.create({
        name: name.trim(),
        parent_id: parent || null,
        tokens: parsePairs(tokens),
        locked_tokens: locks.split(",").map((k) => k.trim()).filter(Boolean),
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.brandNodes });
      onDone();
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
        <input id={nameId} className="cf-input" value={name} onChange={(e) => setName(e.target.value)} required />
      </div>
      <div className="cf-field">
        <label className="cf-field__label" htmlFor={parentId}>
          Parent
        </label>
        <select id={parentId} className="cf-input" value={parent} onChange={(e) => setParent(e.target.value)}>
          <option value="">None — this is a parent brand</option>
          {nodes.map((n) => (
            <option key={n.id} value={n.id}>
              {n.name}
            </option>
          ))}
        </select>
      </div>
      <div className="cf-field">
        <label className="cf-field__label" htmlFor={tokensId}>
          Token overrides
        </label>
        <textarea id={tokensId} className="cf-input" rows={3} value={tokens} onChange={(e) => setTokens(e.target.value)} placeholder={"color.accent=#0044cc\nfont.body=Inter"} />
        <p className="cf-field__description">One key=value per line.</p>
      </div>
      <div className="cf-field">
        <label className="cf-field__label" htmlFor={locksId}>
          Lock for children
        </label>
        <input id={locksId} className="cf-input" value={locks} onChange={(e) => setLocks(e.target.value)} placeholder="color.accent" />
        <p className="cf-field__description">Comma-separated token keys children may not override.</p>
      </div>
      {create.isError && (
        <p className="cf-error__detail" role="alert">
          {isApiError(create.error) ? create.error.detail : "Could not create the brand node."}
        </p>
      )}
      <button type="submit" className="cf-button cf-button--primary cf-button--md" disabled={create.isPending}>
        {create.isPending ? "Creating…" : "Create node"}
      </button>
    </form>
  );
}

function EffectiveView({ nodeId }: { nodeId: string }) {
  const eff = useQuery(brandEffectiveQuery(nodeId));
  if (eff.isPending) return <LoadingState label="Computing effective brand…" />;
  if (eff.isError) return <ErrorState {...(isApiError(eff.error) ? { detail: eff.error.detail } : {})} retry={() => void eff.refetch()} />;
  const rows = [
    ...Object.entries(eff.data.tokens).map(([k, v]) => ({ kind: "token", k, v })),
    ...Object.entries(eff.data.policies).map(([k, v]) => ({ kind: "policy", k, v })),
  ];
  return (
    <div className="cf-runs__tablewrap">
      <table className="cf-runs">
        <caption className="cf-visually-hidden">Effective brand values at this node</caption>
        <thead>
          <tr>
            <th scope="col">Kind</th>
            <th scope="col">Key</th>
            <th scope="col">Effective value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.kind}:${r.k}`}>
              <td>{r.kind}</td>
              <th scope="row">{r.k}</th>
              <td>{r.v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
