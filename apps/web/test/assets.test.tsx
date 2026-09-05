import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { SequenceSummary } from "../src/api/types";
import { authenticated } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

const SEQ: SequenceSummary = {
  name: "holding-hands",
  anchor: true,
  frames: [
    { index: 0, file: "frames/0000.png", attempts: 1, cache_hit: false, drift: { locked: 0.91, style: 0.05 } },
    { index: 1, file: "frames/0001.png", attempts: 2, cache_hit: false, drift: { locked: 0.88, style: 0.07 } },
  ],
  videos: ["preview.mp4", "holding-hands.mp4"],
  contact_sheet: true,
  flipbook: true,
  updated_at: 1_756_000_000,
};

describe("assets page", () => {
  it("shows an honest empty state that promises live refresh", async () => {
    server.use(authenticated());
    renderApp("/assets");
    expect(await screen.findByText("Nothing generated yet")).toBeInTheDocument();
    expect(screen.getByText(/refreshes itself while a run is in progress/)).toBeInTheDocument();
  });

  it("renders a sequence with video, anchor, frames, and drift numbers", async () => {
    server.use(authenticated(), http.get("*/v1/sequences", () => HttpResponse.json([SEQ])));
    renderApp("/assets");

    const card = await screen.findByRole("region", { name: "Sequence holding-hands" });
    expect(within(card).getByText(/2 frames · video ready/)).toBeInTheDocument();
    // The final clip is preferred over the raw preview.
    const video = card.querySelector("video");
    expect(video?.getAttribute("src")).toContain("/v1/sequences/holding-hands/files/holding-hands.mp4");
    expect(within(card).getByAltText("holding-hands anchor frame")).toBeInTheDocument();
    expect(within(card).getByAltText("frame 1")).toBeInTheDocument();
    expect(within(card).getByText("1 · lock 0.91")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Contact sheet" })).toHaveAttribute(
      "href",
      expect.stringContaining("/files/contact-sheet.png"),
    );
  });
});
