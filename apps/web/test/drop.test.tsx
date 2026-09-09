import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { authenticated, DROPPED_AUDIO, DROPPED_VIDEO, graphStore, uploadPosts } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

beforeEach(() => {
  window.localStorage.removeItem("cf.workspace.graphs.v1");
  window.localStorage.removeItem("cf.workspace.active.v1");
  server.use(authenticated());
});

/** jsdom has no DataTransfer with files, so the drop event carries the shape React reads. */
function dropFile(target: Element, file: File) {
  fireEvent.drop(target, {
    dataTransfer: { files: [file], items: [], types: ["Files"], getData: () => "" },
  });
}

const take = () => new File([new Uint8Array([1, 2, 3, 4])], "take one.wav", { type: "audio/wav" });

async function openCanvas() {
  renderApp("/workspace");
  await screen.findAllByText("Campaign Brief");
  return screen.getByRole("application", { name: /Graph:/ });
}

describe("dropping a file on the canvas", () => {
  it("uploads it, says what it is, and spawns the node that holds it", async () => {
    const canvas = await openCanvas();

    dropFile(canvas, take());

    await waitFor(() => expect(uploadPosts).toEqual(["take one.wav"]));
    // The node that can hold a recording, on the canvas (the library lists the type too).
    expect(await within(canvas).findByText("Audio File")).toBeInTheDocument();
    const dropped = await screen.findByRole("region", { name: "Dropped files" });
    expect(within(dropped).getByText("take one.wav")).toBeInTheDocument();
    // Measured, not guessed: this is the line that tells the operator what arrived.
    expect(within(dropped).getByText(DROPPED_AUDIO.description)).toBeInTheDocument();

    await waitFor(
      () => {
        const nodes = [...graphStore.values()].flatMap(
          (doc) => (doc as { nodes?: { type: string; values?: Record<string, unknown> }[] }).nodes ?? [],
        );
        const node = nodes.find((n) => n.type === "input.audio");
        expect(node?.values?.asset).toBe(DROPPED_AUDIO.asset_id);
        expect(node?.values?.filename).toBe("take one.wav");
      },
      { timeout: 4000 },
    );
  });

  it("offers the next steps with their reasons, and spawns one already wired", async () => {
    const user = userEvent.setup();
    const canvas = await openCanvas();
    dropFile(canvas, take());

    const dropped = await screen.findByRole("region", { name: "Dropped files" });
    // The reason comes from what was measured, so it is specific to this file.
    expect(within(dropped).getByText(/16 kHz mono/)).toBeInTheDocument();

    await user.click(within(dropped).getByRole("button", { name: "Read what it says" }));

    expect(await within(canvas).findByText("Transcribe Recording")).toBeInTheDocument();
    // Wired, not merely placed: the link is in the document the server receives.
    type Doc = {
      nodes?: { id: string; type: string }[];
      links?: { from_node: string; to_node: string; from_slot: string; to_slot: string }[];
    };
    await waitFor(
      () => {
        const wired = [...graphStore.values()].some((raw) => {
          const doc = raw as Doc;
          const source = doc.nodes?.find((n) => n.type === "input.audio");
          const clean = doc.nodes?.find((n) => n.type === "transcribe_audio");
          return Boolean(
            source &&
              clean &&
              doc.links?.some(
                (l) =>
                  l.from_node === source.id &&
                  l.to_node === clean.id &&
                  l.from_slot === "audio" &&
                  l.to_slot === "audio",
              ),
          );
        });
        expect(wired).toBe(true);
      },
      { timeout: 4000 },
    );
    // A step already taken is not offered twice.
    expect(within(dropped).getByRole("button", { name: "Added" })).toBeDisabled();
  });

  it("says when the file it stored is not the file that was dropped", async () => {
    const canvas = await openCanvas();

    dropFile(canvas, new File(["MKV-BYTES"], "2026-09-09 13-35-29.mkv", { type: "video/x-matroska" }));

    const dropped = await screen.findByRole("region", { name: /Dropped/ });
    // The node holds an MP4 now; the strip says so instead of leaving it to be discovered.
    expect(await within(dropped).findByText(/converted to mp4/)).toBeInTheDocument();
    expect(within(dropped).getByText(/no re-encode/)).toBeInTheDocument();
    expect(within(dropped).getByText(DROPPED_VIDEO.description)).toBeInTheDocument();
    expect(await within(canvas).findByText("Video File")).toBeInTheDocument();

    await waitFor(
      () => {
        const nodes = [...graphStore.values()].flatMap(
          (doc) => (doc as { nodes?: { type: string; values?: Record<string, unknown> }[] }).nodes ?? [],
        );
        const node = nodes.find((n) => n.type === "input.video");
        expect(node?.values?.filename).toBe("2026-09-09 13-35-29.mp4");
      },
      { timeout: 4000 },
    );
  });

  it("says why a refused file was refused, and spawns nothing", async () => {
    const canvas = await openCanvas();
    dropFile(canvas, new File(["<svg/>"], "logo.svg", { type: "image/svg+xml" }));

    const dropped = await screen.findByRole("region", { name: "Dropped files" });
    expect(await within(dropped).findByText(/sniffed, not extension-based/)).toBeInTheDocument();
    expect(within(canvas).queryByText("Audio File")).not.toBeInTheDocument();
  });
});
