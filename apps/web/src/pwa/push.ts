/** Web-push helpers, kept separate from React so they unit-test cleanly. */

/** Convert a base64url-encoded VAPID public key into the bytes PushManager expects. */
export function base64UrlToUint8Array(base64Url: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64Url.length % 4)) % 4);
  const base64 = (base64Url + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

/** The active registration, or null when the browser has none (dev builds skip registering). */
export async function getPushRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
  return (await navigator.serviceWorker.getRegistration()) ?? null;
}

export interface SubscriptionJson {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

/** Validate the browser's PushSubscription JSON into the shape the API takes. */
export function toSubscriptionJson(subscription: PushSubscription): SubscriptionJson {
  const json = subscription.toJSON();
  const p256dh = json.keys?.["p256dh"];
  const auth = json.keys?.["auth"];
  if (!json.endpoint || !p256dh || !auth) throw new Error("The browser returned an incomplete push subscription.");
  return { endpoint: json.endpoint, keys: { p256dh, auth } };
}
