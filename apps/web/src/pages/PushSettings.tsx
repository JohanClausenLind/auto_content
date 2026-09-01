import { Button } from "@content-factory/web-ui";
import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, isApiError } from "../api/client";
import { base64UrlToUint8Array, getPushRegistration, toSubscriptionJson } from "../pwa/push";
import { LoadingState } from "./EmptyState";

type SwState =
  | { phase: "checking" }
  | { phase: "unsupported" }
  | { phase: "no-registration" }
  | { phase: "ready"; registration: ServiceWorkerRegistration };

function enableErrorMessage(error: unknown): string {
  if (isApiError(error) && error.status === 503) return `Push isn't configured on this server yet (${error.detail}). Run setup to generate keys, then come back.`;
  if (isApiError(error)) return error.detail;
  if (error instanceof Error && error.name === "NotAllowedError") return "Notifications are blocked for this site. Allow them in your browser settings, then try again.";
  if (error instanceof Error && error.message) return error.message;
  return "Couldn't enable push on this device.";
}

export function PushSettings() {
  const [sw, setSw] = useState<SwState>({ phase: "checking" });
  const [subscription, setSubscription] = useState<PushSubscription | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) {
        if (!cancelled) setSw({ phase: "unsupported" });
        return;
      }
      const registration = await getPushRegistration();
      if (cancelled) return;
      if (!registration) {
        setSw({ phase: "no-registration" });
        return;
      }
      setSw({ phase: "ready", registration });
      const existing = await registration.pushManager.getSubscription();
      if (!cancelled) setSubscription(existing);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const enable = useMutation({
    mutationFn: async () => {
      if (sw.phase !== "ready") throw new Error("No service worker is registered in this session.");
      const { public_key } = await api.notifications.vapidPublicKey();
      const sub = await sw.registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64UrlToUint8Array(public_key),
      });
      const json = toSubscriptionJson(sub);
      await api.notifications.subscribe({ ...json, user_agent: navigator.userAgent });
      return sub;
    },
    onSuccess: (sub) => setSubscription(sub),
  });

  const sendTest = useMutation({ mutationFn: api.notifications.test });

  const disable = useMutation({
    mutationFn: async () => {
      if (!subscription) return;
      const endpoint = subscription.endpoint;
      await subscription.unsubscribe();
      await api.notifications.unsubscribe(endpoint);
    },
    onSuccess: () => {
      setSubscription(null);
      sendTest.reset();
    },
  });

  return (
    <section className="cf-push" aria-labelledby="cf-push-title">
      <h2 id="cf-push-title" className="cf-push__title">
        Push notifications
      </h2>
      <p className="cf-push__body">
        Push tells you when a run needs a decision, finishes, or fails. A notification is only ever a link back into the app — it never approves anything by itself. Every decision still
        happens here, signed in.
      </p>

      {sw.phase === "checking" && <LoadingState label="Checking this browser…" />}

      {sw.phase === "unsupported" && <p className="cf-push__note">This browser doesn't support web push, so there's nothing to enable here.</p>}

      {sw.phase === "no-registration" && (
        <p className="cf-push__note">
          No service worker is registered in this session, so push can't be enabled. In development the app skips the service worker on purpose — build and preview the production bundle to
          try push on this device.
        </p>
      )}

      {sw.phase === "ready" && (
        <div className="cf-push__controls">
          {subscription === null ? (
            <>
              <Button variant="primary" onPress={() => enable.mutate()} isDisabled={enable.isPending} isPending={enable.isPending}>
                {enable.isPending ? "Enabling…" : "Enable push on this device"}
              </Button>
              <p className="cf-push__note">Your browser will ask for permission once. You can turn this off here at any time.</p>
            </>
          ) : (
            <>
              <p className="cf-push__status" role="status">
                Push is on for this device.
              </p>
              <div className="cf-push__buttons">
                <Button variant="secondary" onPress={() => sendTest.mutate()} isDisabled={sendTest.isPending} isPending={sendTest.isPending}>
                  {sendTest.isPending ? "Sending…" : "Send test notification"}
                </Button>
                <Button variant="secondary" onPress={() => disable.mutate()} isDisabled={disable.isPending} isPending={disable.isPending}>
                  {disable.isPending ? "Disabling…" : "Disable on this device"}
                </Button>
              </div>
              {sendTest.isSuccess && (
                <p className="cf-push__result" role="status">
                  {sendTest.data.total === 0
                    ? "No devices are registered for your account yet."
                    : `Sent to ${sendTest.data.sent} of ${sendTest.data.total} device${sendTest.data.total === 1 ? "" : "s"}.`}
                </p>
              )}
              {sendTest.isError && (
                <p className="cf-push__error" role="alert">
                  {enableErrorMessage(sendTest.error)}
                </p>
              )}
            </>
          )}
          {enable.isError && (
            <p className="cf-push__error" role="alert">
              {enableErrorMessage(enable.error)}
            </p>
          )}
          {disable.isError && (
            <p className="cf-push__error" role="alert">
              Couldn't fully disable push. Reload and try again.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
