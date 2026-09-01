import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, isApiError } from "../api/client";
import { sequencesQuery } from "../api/queries";
import type { SequenceSummary } from "../api/types";
import { EmptyState, ErrorState, LoadingState, Page } from "./EmptyState";

/** Generated media for review: image sequences with their anchor, frames, drift, and video. */
export function AssetsPage() {
  const sequences = useQuery(sequencesQuery);
  return (
    <Page title="Assets" lead="What the factory has generated, fresh from the models — review it here.">
      {sequences.isPending ? (
        <LoadingState label="Loading generated assets…" />
      ) : sequences.isError ? (
        <ErrorState {...(isApiError(sequences.error) ? { detail: sequences.error.detail } : {})} retry={() => void sequences.refetch()} />
      ) : sequences.data.length === 0 ? (
        <EmptyState
          title="Nothing generated yet"
          body="Image sequences and renders appear here as soon as a generation run writes its first frame — the page refreshes itself while a run is in progress."
        />
      ) : (
        sequences.data.map((seq) => <SequenceCard key={seq.name} seq={seq} />)
      )}
    </Page>
  );
}

function SequenceCard({ seq }: { seq: SequenceSummary }) {
  const [showFrames, setShowFrames] = useState(true);
  const video = seq.videos.find((v) => v !== "preview.mp4") ?? seq.videos[0];
  return (
    <section className="cf-seq" aria-label={`Sequence ${seq.name}`}>
      <header className="cf-seq__header">
        <h2 className="cf-seq__title">{seq.name}</h2>
        <span className="cf-personas__rev">
          {seq.frames.length} frame{seq.frames.length === 1 ? "" : "s"}
          {seq.videos.length > 0 ? " · video ready" : " · generating"}
        </span>
      </header>
      {video && (
        // eslint-disable-next-line jsx-a11y/media-has-caption -- generated silent clip
        <video className="cf-seq__video" controls loop src={api.sequences.fileUrl(seq.name, video)} />
      )}
      <div className="cf-seq__strip">
        {seq.anchor && (
          <figure className="cf-seq__thumb cf-seq__thumb--anchor">
            <img src={api.sequences.fileUrl(seq.name, "anchor.png")} alt={`${seq.name} anchor image`} loading="lazy" />
            <figcaption>anchor</figcaption>
          </figure>
        )}
        {showFrames &&
          seq.frames.map((f) => (
            <figure key={f.index} className="cf-seq__thumb">
              <img src={api.sequences.fileUrl(seq.name, f.file)} alt={`frame ${f.index + 1}`} loading="lazy" />
              <figcaption>
                {f.index + 1}
                {f.drift ? ` · lock ${f.drift.locked.toFixed(2)}` : ""}
              </figcaption>
            </figure>
          ))}
      </div>
      <div className="cf-requests__actions">
        {seq.frames.length > 0 && (
          <button type="button" className="cf-button cf-button--ghost cf-button--sm" onClick={() => setShowFrames((s) => !s)}>
            {showFrames ? "Hide frames" : "Show frames"}
          </button>
        )}
        {seq.contact_sheet && (
          <a className="cf-button cf-button--secondary cf-button--sm" href={api.sequences.fileUrl(seq.name, "contact-sheet.png")} target="_blank" rel="noreferrer">
            Contact sheet
          </a>
        )}
        {seq.flipbook && (
          <a className="cf-button cf-button--secondary cf-button--sm" href={api.sequences.fileUrl(seq.name, "flipbook.pdf")} target="_blank" rel="noreferrer">
            Flipbook PDF
          </a>
        )}
      </div>
    </section>
  );
}
