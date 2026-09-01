import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { groupRunsByDay } from "../src/pages/CalendarPage";
import type { RunSummary } from "../src/api/types";
import { authenticated } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

const mocks = vi.hoisted(() => ({ getPushRegistration: vi.fn() }));
vi.mock("../src/pwa/push", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/pwa/push")>();
  return { ...actual, getPushRegistration: mocks.getPushRegistration };
});

function makeRun(overrides: Partial<RunSummary>): RunSummary {
  return {
    run_id: "run_1",
    state: "COMPLETE",
    campaign_id: "cmp_00000001",
    project_id: "prj_00000001",
    quality: "demo",
    created_at: "2026-09-01T09:00:00Z",
    error: null,
    ...overrides,
  } as RunSummary;
}

afterEach(() => {
  mocks.getPushRegistration.mockReset();
});

describe("calendar page", () => {
  it("groups runs by day, newest day first", () => {
    const groups = groupRunsByDay([
      makeRun({ run_id: "a", created_at: "2026-08-30T08:00:00Z" }),
      makeRun({ run_id: "b", created_at: "2026-09-01T10:00:00Z" }),
      makeRun({ run_id: "c", created_at: "2026-09-01T07:00:00Z" }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0]?.runs.map((r) => r.run_id)).toEqual(["b", "c"]);
    expect(groups[1]?.runs.map((r) => r.run_id)).toEqual(["a"]);
  });

  it("is honest that scheduling doesn't exist and links each run", async () => {
    server.use(
      authenticated(),
      http.get("*/v1/runs", () => HttpResponse.json([makeRun({ run_id: "run_cal_1" })])),
    );
    renderApp("/calendar");
    expect(await screen.findByText("Scheduling isn't built yet")).toBeInTheDocument();
    const link = await screen.findByRole("link", { name: "run_cal_1" });
    expect(link).toHaveAttribute("href", "/projects/run_cal_1");
  });
});

describe("push settings", () => {
  it("explains itself when the browser has no service worker", async () => {
    server.use(authenticated());
    mocks.getPushRegistration.mockResolvedValue(null);
    renderApp("/settings?tab=notifications");
    // jsdom exposes navigator.serviceWorker only if we define it; without it → unsupported.
    expect(await screen.findByText(/doesn't support web push|No service worker is registered/)).toBeInTheDocument();
    // Push never carries an approval action — the copy promises link-only notifications.
    expect(screen.getByText(/never approves anything by itself/)).toBeInTheDocument();
  });

  it("enables push, sends the browser subscription to the server, and can test it", async () => {
    const user = userEvent.setup();
    const fakeSub = {
      endpoint: "https://push.example/send/abc",
      unsubscribe: vi.fn().mockResolvedValue(true),
      toJSON: () => ({ endpoint: "https://push.example/send/abc", keys: { p256dh: "pk", auth: "ak" } }),
    };
    const pushManager = {
      getSubscription: vi.fn().mockResolvedValue(null),
      subscribe: vi.fn().mockResolvedValue(fakeSub),
    };
    Object.defineProperty(navigator, "serviceWorker", { value: {}, configurable: true });
    mocks.getPushRegistration.mockResolvedValue({ pushManager } as unknown as ServiceWorkerRegistration);
    const posted: unknown[] = [];
    server.use(
      authenticated(),
      http.get("*/v1/notifications/vapid-public-key", () => HttpResponse.json({ public_key: "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM" })),
      http.post("*/v1/notifications/subscriptions", async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json({ id: "sub_1" }, { status: 201 });
      }),
      http.post("*/v1/notifications/test", () => HttpResponse.json({ sent: 1, total: 1 })),
    );
    renderApp("/settings?tab=notifications");

    await user.click(await screen.findByRole("button", { name: "Enable push on this device" }));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ endpoint: "https://push.example/send/abc" });
    expect(pushManager.subscribe).toHaveBeenCalledWith(expect.objectContaining({ userVisibleOnly: true }));
    expect(await screen.findByRole("status")).toHaveTextContent("Push is on for this device.");

    await user.click(screen.getByRole("button", { name: "Send test notification" }));
    expect(await screen.findByText("Sent to 1 of 1 device.")).toBeInTheDocument();
  });
});

describe("assistant panel", () => {
  it("opens from the top bar and lists the MCP tools honestly, with no fake chat", async () => {
    const user = userEvent.setup();
    server.use(authenticated());
    renderApp("/");
    await user.click(await screen.findByRole("button", { name: "Assistant" }));
    const dialog = await screen.findByRole("dialog", { name: "Assistant" });
    expect(within(dialog).getByText(/no chat here/)).toBeInTheDocument();
    expect(within(dialog).getByText("approve_preflight")).toBeInTheDocument();
    expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
  });
});
