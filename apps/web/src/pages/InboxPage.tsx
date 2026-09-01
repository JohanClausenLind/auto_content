import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api, isApiError } from "../api/client";
import { fanInboxQuery, queryKeys } from "../api/queries";
import type { FanMessage } from "../api/types";
import { useT } from "../i18n";
import { EmptyState, ErrorState, LoadingState, Page } from "./EmptyState";

const CLASS_LABEL: Record<string, string> = {
  question: "Question",
  casual_question: "Casual",
  compliment: "Compliment",
  request: "Request",
  spam: "Spam",
  harassment: "Harassment",
  safety_relevant: "Safety",
  emoji_only: "Emoji",
  other: "Other",
};

/** Fan messages pulled by the read adapters. Every message gets answered or skipped-with-reason. */
export function InboxPage() {
  const t = useT();
  const client = useQueryClient();
  const inbox = useQuery(fanInboxQuery);
  const sync = useMutation({
    mutationFn: () => api.engagement.sync(),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.fanInbox }),
  });

  return (
    <Page title={t("inbox.title")} lead={t("inbox.lead")}>
      <div className="cf-personas__toolbar">
        <button type="button" className="cf-button cf-button--secondary cf-button--sm" disabled={sync.isPending} onClick={() => sync.mutate()}>
          {sync.isPending ? t("inbox.checking") : t("inbox.check")}
        </button>
      </div>
      {sync.isError && (
        <p className="cf-error__detail" role="alert">
          {isApiError(sync.error) ? sync.error.detail : "Could not check for messages."}
        </p>
      )}
      {inbox.isPending ? (
        <LoadingState label={t("common.loading")} />
      ) : inbox.isError ? (
        <ErrorState {...(isApiError(inbox.error) ? { detail: inbox.error.detail } : {})} retry={() => void inbox.refetch()} />
      ) : inbox.data.length === 0 ? (
        <EmptyState title={t("inbox.empty.title")} body={t("inbox.empty.body")} />
      ) : (
        <ul className="cf-inbox__list">
          {inbox.data.map((m) => (
            <MessageRow key={m.id} message={m} />
          ))}
        </ul>
      )}
    </Page>
  );
}

function MessageRow({ message }: { message: FanMessage }) {
  const t = useT();
  const client = useQueryClient();
  const reasonId = useId();
  const [skipping, setSkipping] = useState(false);
  const [reason, setReason] = useState("");
  const decide = useMutation({
    mutationFn: (args: { disposition: "answered" | "skipped"; reason?: string }) =>
      api.engagement.setDisposition(message.id, args.disposition, args.reason),
    onSuccess: () => void client.invalidateQueries({ queryKey: queryKeys.fanInbox }),
  });
  const urgent = message.message_class === "safety_relevant" || message.message_class === "harassment";
  return (
    <li className="cf-inbox__item" data-urgent={urgent || undefined}>
      <div className="cf-inbox__meta">
        <span className="cf-inbox__class" data-class={message.message_class}>
          {CLASS_LABEL[message.message_class] ?? message.message_class}
        </span>
        {message.vip && <span className="cf-inbox__vip">VIP</span>}
        <span className="cf-personas__rev">
          {message.fan_id} · {message.platform}
        </span>
      </div>
      <p className="cf-inbox__text">{message.text}</p>
      {urgent ? (
        <p className="cf-inbox__guidance">{t("inbox.urgent")}</p>
      ) : null}
      <div className="cf-requests__actions">
        <button
          type="button"
          className="cf-button cf-button--secondary cf-button--sm"
          disabled={decide.isPending}
          onClick={() => decide.mutate({ disposition: "answered" })}
        >
          {t("inbox.markAnswered")}
        </button>
        <button type="button" className="cf-button cf-button--ghost cf-button--sm" onClick={() => setSkipping((s) => !s)}>
          {skipping ? t("inbox.cancelSkip") : t("inbox.skip")}
        </button>
      </div>
      {skipping && (
        <form
          className="cf-inbox__skip"
          onSubmit={(e) => {
            e.preventDefault();
            if (reason.trim() && !decide.isPending) decide.mutate({ disposition: "skipped", reason: reason.trim() });
          }}
        >
          <div className="cf-field">
            <label className="cf-field__label" htmlFor={reasonId}>
              {t("inbox.skipReasonLabel")}
            </label>
            <input id={reasonId} className="cf-input" value={reason} onChange={(e) => setReason(e.target.value)} required />
          </div>
          <button type="submit" className="cf-button cf-button--secondary cf-button--sm" disabled={!reason.trim() || decide.isPending}>
            {t("inbox.skipWithReason")}
          </button>
        </form>
      )}
      {decide.isError && (
        <p className="cf-error__detail" role="alert">
          {isApiError(decide.error) ? decide.error.detail : "Could not update the message."}
        </p>
      )}
    </li>
  );
}
