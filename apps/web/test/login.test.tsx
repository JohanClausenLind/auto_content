import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { makeSession } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

describe("login flow", () => {
  it("signs in with a password and lands in the shell with the workspace name", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("*/v1/session", async ({ request }) => {
        const body = (await request.json()) as { username: string; password: string };
        if (body.username === "vega" && body.password === "correct horse") return HttpResponse.json(makeSession());
        return HttpResponse.json({ detail: "Wrong username or password." }, { status: 401 });
      }),
    );
    renderApp("/login");
    await screen.findByRole("heading", { name: "Sign in to Content Factory" });

    await user.type(screen.getByLabelText("Username"), "vega");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong username or password.");

    await user.clear(screen.getByLabelText("Password"));
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByRole("navigation", { name: "Areas" });
    expect(screen.getByRole("button", { name: /Workspace: Acme Studio/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Good to see you, Vega Operator.");
  });

  it("asks for a TOTP code when the server requires a second factor", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("*/v1/session", () => HttpResponse.json({ mfa_required: true, methods: ["totp", "passkey"] }, { status: 202 })),
      http.post("*/v1/session/totp", async ({ request }) => {
        const { code } = (await request.json()) as { code: string };
        return code === "123456" ? HttpResponse.json(makeSession()) : HttpResponse.json({ detail: "Code expired." }, { status: 401 });
      }),
    );
    renderApp("/login");
    await user.type(await screen.findByLabelText("Username"), "vega");
    await user.type(screen.getByLabelText("Password"), "pw");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await screen.findByRole("heading", { name: "One more step" });
    await user.type(screen.getByLabelText(/Authenticator code/), "123456");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(screen.getByRole("navigation", { name: "Areas" })).toBeInTheDocument());
  });

  it("offers a passkey button", async () => {
    renderApp("/login");
    expect(await screen.findByRole("button", { name: "Use a passkey" })).toBeInTheDocument();
  });
});

describe("auth guard", () => {
  it("redirects to /login when the session is 401", async () => {
    const { router } = renderApp("/calendar");
    await screen.findByRole("heading", { name: "Sign in to Content Factory" });
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.search).toMatchObject({ next: "/calendar" });
  });
});
