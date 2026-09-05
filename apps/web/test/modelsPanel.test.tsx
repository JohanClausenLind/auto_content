import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { authenticated, downloadPosts, installPosts } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

beforeEach(() => {
  window.localStorage.removeItem("cf.workspace.graphs.v1");
  window.localStorage.removeItem("cf.workspace.active.v1");
  downloadPosts.length = 0;
});

async function openModels(user: ReturnType<typeof userEvent.setup>) {
  server.use(authenticated());
  renderApp("/workspace");
  await screen.findAllByText("Campaign Brief");
  await user.click(screen.getByRole("button", { name: "Toggle model library" }));
  return await screen.findByRole("complementary", { name: "Model library" });
}

describe("models panel", () => {
  it("lists installed models grouped by kind with sizes, searchable", async () => {
    const user = userEvent.setup();
    const panel = await openModels(user);

    const installed = within(panel).getByRole("region", { name: "Installed models" });
    expect(within(installed).getByRole("button", { name: "vae2" })).toBeInTheDocument();
    expect(within(installed).getByText("ltx-2.5-video-vae-conv-bf16.safetensors")).toBeInTheDocument();
    expect(within(installed).getByText(/4 files · 1 roots/)).toBeInTheDocument();

    await user.type(within(panel).getByLabelText("Search models"), "audio");
    expect(within(installed).getByText("ltx-2.5-audio-vae-bf16(1).safetensors")).toBeInTheDocument();
    expect(within(installed).queryByText("ltx-2.5-video-vae-conv-bf16.safetensors")).not.toBeInTheDocument();
  });

  it("shows what the workflows still need, each with an install button", async () => {
    const user = userEvent.setup();
    const panel = await openModels(user);
    const missing = within(panel).getByRole("region", { name: "Missing models" });
    // The missing list is the store's, so it covers weights that live outside ComfyUI's tree and
    // skill environments — and every row installs from here instead of printing a command.
    expect(within(missing).getByText(/GIMM-VFI/)).toBeInTheDocument();
    expect(within(missing).getByText(/Kokoro voice/)).toBeInTheDocument();
    expect(within(missing).queryByText(/comfy model download --url/)).not.toBeInTheDocument();

    await user.click(within(missing).getByRole("button", { name: "Install gimm-vfi" }));
    await waitFor(() => expect(installPosts).toEqual(["gimm-vfi"]));
  });

  it("one click on Add-from-URL starts a comfy-cli background download", async () => {
    const user = userEvent.setup();
    const panel = await openModels(user);
    const add = within(panel).getByRole("region", { name: "Add model from URL" });

    await user.type(
      within(add).getByLabelText("Model URL"),
      "https://huggingface.co/acme/pack/resolve/main/style.safetensors",
    );
    expect(within(add).getByText("→ style.safetensors")).toBeInTheDocument();
    await user.selectOptions(within(add).getByLabelText("Model folder"), "loras");
    await user.click(within(add).getByRole("button", { name: "⭳ Download" }));

    await waitFor(() => expect(downloadPosts).toHaveLength(1));
    expect(downloadPosts[0]).toEqual({
      url: "https://huggingface.co/acme/pack/resolve/main/style.safetensors",
      relative_path: "models/loras",
      filename: "style.safetensors",
    });
    // the job shows up in the Downloads section as running
    const downloads = await within(panel).findByRole("region", { name: "Downloads" });
    expect(within(downloads).getByText("style.safetensors")).toBeInTheDocument();
    expect(within(downloads).getByText("running")).toBeInTheDocument();
  });
});
