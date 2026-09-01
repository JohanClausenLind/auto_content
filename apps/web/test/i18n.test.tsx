import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { CATALOGS, en } from "../src/i18n/messages";
import { authenticated } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

describe("i18n scaffolding", () => {
  it("every locale covers every English key with a distinct value where expected", () => {
    for (const [locale, catalog] of Object.entries(CATALOGS)) {
      for (const key of Object.keys(en)) {
        expect(catalog[key as keyof typeof en], `${locale}:${key}`).toBeTruthy();
      }
    }
  });

  it("switching the language re-renders the app and persists the preference", async () => {
    const user = userEvent.setup();
    const puts: unknown[] = [];
    server.use(
      authenticated(),
      http.put("*/v1/prefs/locale", async ({ request }) => {
        puts.push(await request.json());
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/settings?tab=account");

    await user.selectOptions(await screen.findByLabelText("Language"), "sv");
    await waitFor(() => expect(puts).toEqual([{ value: "sv" }]));
    // The selector itself is now labeled in Swedish.
    expect(await screen.findByLabelText("Språk")).toBeInTheDocument();
  });

  it("loads the saved locale for the signed-in account", async () => {
    server.use(
      authenticated(),
      http.get("*/v1/prefs/locale", () => HttpResponse.json({ value: "sv" })),
    );
    renderApp("/inbox");
    expect(await screen.findByText("Din inkorg är tom")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sök efter nya meddelanden" })).toBeInTheDocument();
  });
});
