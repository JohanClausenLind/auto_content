import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { BrandNode, PersonaDetail, PersonaDiff, PortalBriefRow, PortalLinkRow } from "../src/api/types";
import { authenticated } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

function makePersona(overrides: Partial<PersonaDetail> = {}): PersonaDetail {
  return {
    id: "psn_abcdefgh",
    name: "Nova",
    revision: 1,
    archived: false,
    document: {
      persona_id: "psn_abcdefgh",
      revision: 1,
      identity: { display_name: "Nova", pronouns: "they/them", presented_age: 24 },
      voice: {
        tone: ["warm", "formal"],
        vocabulary: "everyday",
        sentence_length: "medium",
        emoji_policy: "rare",
        punctuation: "standard",
        humor: "playful",
        pet_names: [],
      },
      backstory: { bio: "Synthwave and houseplants." },
      disclosure: "disclose_on_ask",
    },
    ...overrides,
  };
}

const DIFF: PersonaDiff = {
  persona_id: "psn_abcdefgh",
  base_revision: 1,
  changes: [{ path: "voice.tone", before: "warm, formal", after: "warm, casual", reason: "operator: register too formal" }],
  plain_language: "voice.tone: warm, formal → warm, casual",
};

describe("personas page", () => {
  it("previews a typed diff and applies it revision-bound", async () => {
    const user = userEvent.setup();
    const persona = makePersona();
    const applied: unknown[] = [];
    server.use(
      authenticated(),
      http.get("*/v1/personas", () => HttpResponse.json([{ id: persona.id, name: persona.name, revision: persona.revision, archived: false }])),
      http.get("*/v1/personas/psn_abcdefgh", () => HttpResponse.json(persona)),
      http.post("*/v1/personas/psn_abcdefgh/revise", () => HttpResponse.json(DIFF)),
      http.post("*/v1/personas/psn_abcdefgh/apply", async ({ request }) => {
        applied.push(await request.json());
        return HttpResponse.json(makePersona({ revision: 2 }));
      }),
    );
    renderApp("/personas");

    await user.click(await screen.findByRole("button", { name: /Nova/ }));
    await user.type(await screen.findByLabelText("Change something"), "too formal");
    await user.click(screen.getByRole("button", { name: "Preview change" }));

    // The diff is shown BEFORE anything changes.
    const region = await screen.findByRole("region", { name: "Proposed change" });
    expect(region).toHaveTextContent("voice.tone");
    expect(region).toHaveTextContent("warm, casual");

    await user.click(screen.getByRole("button", { name: "Apply (revision 2)" }));
    await waitFor(() => expect(applied).toHaveLength(1));
    expect(applied[0]).toMatchObject({ base_revision: 1, persona_id: "psn_abcdefgh" });
  });

  it("explains a stale diff instead of merging it", async () => {
    const user = userEvent.setup();
    const persona = makePersona();
    server.use(
      authenticated(),
      http.get("*/v1/personas", () => HttpResponse.json([{ id: persona.id, name: persona.name, revision: 1, archived: false }])),
      http.get("*/v1/personas/psn_abcdefgh", () => HttpResponse.json(persona)),
      http.post("*/v1/personas/psn_abcdefgh/revise", () => HttpResponse.json(DIFF)),
      http.post("*/v1/personas/psn_abcdefgh/apply", () => HttpResponse.json({ detail: "stale" }, { status: 409 })),
    );
    renderApp("/personas");
    await user.click(await screen.findByRole("button", { name: /Nova/ }));
    await user.type(await screen.findByLabelText("Change something"), "too formal");
    await user.click(screen.getByRole("button", { name: "Preview change" }));
    await user.click(await screen.findByRole("button", { name: "Apply (revision 2)" }));
    expect(await screen.findByText(/changed since this preview/)).toBeInTheDocument();
  });
});

describe("brand page", () => {
  it("shows the tree with locks and surfaces a lock conflict on create", async () => {
    const user = userEvent.setup();
    const nodes: BrandNode[] = [
      { id: "brand_root0001", parent_id: null, name: "Parent Brand", tokens: { "color.accent": "#0044cc" }, locked_tokens: ["color.accent"], policies: {}, locked_policies: [] },
    ];
    server.use(
      authenticated(),
      http.get("*/v1/brand-nodes", () => HttpResponse.json(nodes)),
      http.get("*/v1/brand-nodes/brand_root0001/effective", () => HttpResponse.json({ tokens: { "color.accent": "#0044cc" }, policies: {} })),
      http.post("*/v1/brand-nodes", () => HttpResponse.json({ detail: "locked upstream, cannot override here: tokens=['color.accent'] policies=[]" }, { status: 409 })),
    );
    renderApp("/brand");

    await user.click(await screen.findByRole("button", { name: /Parent Brand/ }));
    expect(await screen.findByRole("row", { name: /color\.accent/ })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add brand node" }));
    await user.type(screen.getByLabelText("Name"), "Stockholm");
    await user.type(screen.getByLabelText("Token overrides"), "color.accent=#ff0000");
    await user.click(screen.getByRole("button", { name: "Create node" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("locked upstream");
  });
});

describe("requests page", () => {
  it("lists briefs, decides them, and mints a one-time link", async () => {
    const user = userEvent.setup();
    const briefs: PortalBriefRow[] = [
      { id: "brief_00000001", topic: "Autumn teaser", objective: "45s clip", deadline: null, contact: "client@example.com", status: "new", created_at: "2026-09-01T08:00:00Z" },
    ];
    const links: PortalLinkRow[] = [];
    const decisions: unknown[] = [];
    server.use(
      authenticated(),
      http.get("*/v1/portal-briefs", () => HttpResponse.json(briefs)),
      http.get("*/v1/portal-links", () => HttpResponse.json(links)),
      http.post("*/v1/portal-briefs/brief_00000001/decision", async ({ request }) => {
        decisions.push(await request.json());
        return HttpResponse.json({ ...briefs[0], status: "accepted" });
      }),
      http.post("*/v1/portal-links", () =>
        HttpResponse.json({ id: "plink_0001", label: "Q3 client", expires_at: "2026-10-01T00:00:00Z", revoked: false, token: "tok.sig", submit_url: "http://localhost:3000/portal?token=tok.sig" }, { status: 201 }),
      ),
    );
    renderApp("/requests");

    expect(await screen.findByRole("row", { name: /Autumn teaser/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(decisions).toEqual([{ decision: "accepted" }]));

    await user.type(screen.getByLabelText("Who is this link for?"), "Q3 client");
    await user.click(screen.getByRole("button", { name: "Mint link" }));
    // The plaintext link is shown exactly once, with a copy-now warning.
    expect(await screen.findByRole("status")).toHaveTextContent("won't be shown again");
    expect(screen.getByRole("status")).toHaveTextContent("token=tok.sig");
  });
});
