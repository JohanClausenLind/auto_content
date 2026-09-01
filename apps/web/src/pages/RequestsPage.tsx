import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api, isApiError } from "../api/client";
import { portalBriefsQuery, portalLinksQuery, queryKeys } from "../api/queries";
import type { PortalLinkCreated } from "../api/types";
import { EmptyState, ErrorState, LoadingState, Page } from "./EmptyState";

/** External request portal: mint submit links, triage the briefs that come in. */
export function RequestsPage() {
  return (
    <Page title="Requests" lead="Briefs submitted from outside, and the links that allow submitting them.">
      <Briefs />
      <Links />
    </Page>
  );
}

function Briefs() {
  const client = useQueryClient();
  const briefs = useQuery(portalBriefsQuery);
  const decide = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "accepted" | "declined" }) => api.portal.decide(id, decision),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.portalBriefs }),
  });
  if (briefs.isPending) return <LoadingState label="Loading briefs…" />;
  if (briefs.isError)
    return <ErrorState {...(isApiError(briefs.error) ? { detail: briefs.error.detail } : {})} retry={() => void briefs.refetch()} />;
  if (briefs.data.length === 0)
    return <EmptyState title="No briefs yet" body="When someone submits through a portal link, their request appears here for you to accept or decline." />;
  return (
    <section aria-label="Submitted briefs" className="cf-requests__briefs">
      <div className="cf-runs__tablewrap">
        <table className="cf-runs">
          <caption className="cf-visually-hidden">Submitted briefs</caption>
          <thead>
            <tr>
              <th scope="col">Topic</th>
              <th scope="col">Objective</th>
              <th scope="col">Contact</th>
              <th scope="col">Status</th>
              <th scope="col">
                <span className="cf-visually-hidden">Decision</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {briefs.data.map((b) => (
              <tr key={b.id}>
                <th scope="row">{b.topic}</th>
                <td>{b.objective}</td>
                <td>{b.contact}</td>
                <td>{b.status}</td>
                <td>
                  {b.status === "new" && (
                    <span className="cf-requests__actions">
                      <button
                        type="button"
                        className="cf-button cf-button--primary cf-button--sm"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: b.id, decision: "accepted" })}
                      >
                        Accept
                      </button>
                      <button
                        type="button"
                        className="cf-button cf-button--ghost cf-button--sm"
                        disabled={decide.isPending}
                        onClick={() => decide.mutate({ id: b.id, decision: "declined" })}
                      >
                        Decline
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Links() {
  const client = useQueryClient();
  const links = useQuery(portalLinksQuery);
  const labelId = useId();
  const [label, setLabel] = useState("");
  const [minted, setMinted] = useState<PortalLinkCreated | null>(null);
  const create = useMutation({
    mutationFn: () => api.portal.createLink(label.trim()),
    onSuccess: (link) => {
      setMinted(link);
      setLabel("");
      void client.invalidateQueries({ queryKey: queryKeys.portalLinks });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.portal.revokeLink(id),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.portalLinks }),
  });
  return (
    <section aria-label="Portal links" className="cf-requests__links">
      <h2 className="cf-create__heading">Portal links</h2>
      <form
        className="cf-requests__mint"
        onSubmit={(e) => {
          e.preventDefault();
          if (label.trim() && !create.isPending) create.mutate();
        }}
      >
        <div className="cf-field">
          <label className="cf-field__label" htmlFor={labelId}>
            Who is this link for?
          </label>
          <input id={labelId} className="cf-input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Q3 client" required />
        </div>
        <button type="submit" className="cf-button cf-button--secondary cf-button--sm" disabled={create.isPending || !label.trim()}>
          {create.isPending ? "Minting…" : "Mint link"}
        </button>
        {create.isError && (
          <p className="cf-error__detail" role="alert">
            {isApiError(create.error) ? create.error.detail : "Could not mint the link."}
          </p>
        )}
      </form>
      {minted && (
        <p className="cf-requests__minted" role="status">
          Copy this now — it won't be shown again: <code>{minted.submit_url}</code>
        </p>
      )}
      {links.isPending ? (
        <LoadingState label="Loading links…" />
      ) : links.isError ? (
        <ErrorState {...(isApiError(links.error) ? { detail: links.error.detail } : {})} retry={() => void links.refetch()} />
      ) : links.data.length === 0 ? (
        <p className="cf-field__description">No links yet. A link lets someone submit a brief — nothing more.</p>
      ) : (
        <ul className="cf-requests__linklist">
          {links.data.map((l) => (
            <li key={l.id} className="cf-requests__link">
              <span className="cf-personas__name">{l.label}</span>
              <span className="cf-personas__rev">expires {new Date(l.expires_at).toLocaleDateString()}</span>
              {l.revoked ? (
                <span className="cf-requests__revoked">revoked</span>
              ) : (
                <button type="button" className="cf-button cf-button--ghost cf-button--sm" disabled={revoke.isPending} onClick={() => revoke.mutate(l.id)}>
                  Revoke
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
