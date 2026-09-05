import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { authenticated, hfTokenStore, installPosts, relinkPosts } from "./msw/handlers";
import { server } from "./msw/server";
import { renderApp } from "./render";

beforeEach(() => {
  server.use(authenticated());
});

async function openModelsPage() {
  renderApp("/models");
  return await screen.findByRole("region", { name: "Missing models" });
}

describe("models page", () => {
  it("is reachable from the nav and leads with what is missing", async () => {
    const user = userEvent.setup();
    renderApp("/");
    await user.click(await screen.findByRole("link", { name: /Models/ }));

    const missing = await screen.findByRole("region", { name: "Missing models" });
    // Required first, then the optional one; the installed family is not in this list.
    const names = within(missing)
      .getAllByRole("listitem")
      .map((row) => row.textContent ?? "");
    expect(names[0]).toMatch(/GIMM-VFI/);
    expect(names.join(" ")).toMatch(/Krea 2 Turbo/);
    expect(names.join(" ")).not.toMatch(/LTX-2.5 22B/);
    // A required weight says which workflow is waiting for it and what it will cost.
    expect(within(missing).getByText(/required by image-to-video/)).toBeInTheDocument();
    expect(within(missing).getByText(/290 MB/)).toBeInTheDocument();
  });

  it("installs one family with one click, by registry key", async () => {
    const user = userEvent.setup();
    const missing = await openModelsPage();

    await user.click(within(missing).getByRole("button", { name: "Install gimm-vfi" }));

    await waitFor(() => expect(installPosts).toEqual(["gimm-vfi"]));
    // The job comes back as running and the row shows progress rather than a static label.
    const bar = await within(missing).findByRole("progressbar", { name: /Installing/ });
    expect(bar).toHaveAttribute("aria-valuenow", "10");
  });

  it("installs everything missing at once, skipping what cannot be installed", async () => {
    const user = userEvent.setup();
    const missing = await openModelsPage();

    await user.click(within(missing).getByRole("button", { name: /Install all/ }));

    // Two installable families and the unbuilt skill env — never RIFE, which has no source.
    await waitFor(() => expect(installPosts.length).toBe(3));
    expect(installPosts).toContain("gimm-vfi");
    expect(installPosts).toContain("krea2");
    expect(installPosts).toContain("skill:skills/audio/kokoro");
    expect(installPosts).not.toContain("rife");
  });

  it("says plainly what it cannot install, and why", async () => {
    const missing = await openModelsPage();
    const rife = within(missing)
      .getAllByRole("listitem")
      .find((row) => (row.textContent ?? "").includes("Practical-RIFE"))!;
    expect(within(rife).queryByRole("button", { name: /^Install/ })).not.toBeInTheDocument();
    expect(within(rife).getByText(/Google Drive/)).toBeInTheDocument();
    // A weight that needs someone at Meta to approve access says so before the click, not after.
    expect(await screen.findByText(/access must be granted/)).toBeInTheDocument();
  });

  it("shows the store path, free space and a repair-links button", async () => {
    const user = userEvent.setup();
    await openModelsPage();
    expect(screen.getByText("/mnt/fast/models")).toBeInTheDocument();
    expect(screen.getByText(/620 GB of 937 GB/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Repair all links" }));
    await waitFor(() => expect(relinkPosts).toHaveLength(1));
    expect(await screen.findByText(/1 index links, 1 ComfyUI links/)).toBeInTheDocument();
  });

  it("flags a weight that is on disk but not linked where the pipeline looks", async () => {
    await openModelsPage();
    const installed = screen.getByRole("region", { name: "Installed models" });
    expect(within(installed).getByText(/LTX-2.5 22B/)).toBeInTheDocument();
    // krea2 is part-downloaded with a broken index link: the missing list offers Resume.
    const missing = screen.getByRole("region", { name: "Missing models" });
    expect(within(missing).getByRole("button", { name: "Resume krea2" })).toBeInTheDocument();
  });

  it("opens a family's real source, licence and file list on demand", async () => {
    const user = userEvent.setup();
    await openModelsPage();
    const installed = screen.getByRole("region", { name: "Installed models" });

    await user.click(
      within(installed).getByRole("button", { name: /Details for LTX-2.5 22B/ }),
    );

    expect(within(installed).getByText("elix3r/LTX-2.5-22b-distilled-GGUF")).toBeInTheDocument();
    expect(within(installed).getByText(/1cd163da9028/)).toBeInTheDocument();
    expect(within(installed).getByText("/mnt/fast/models/ltx25")).toBeInTheDocument();
    expect(within(installed).getByText(/linked into ComfyUI/)).toBeInTheDocument();
  });

  it("takes the Hugging Face token gated weights need, and never shows it back", async () => {
    const user = userEvent.setup();
    await openModelsPage();

    const field = screen.getByLabelText(/Hugging Face token/);
    await user.type(field, "hf_pretendtoken123");
    await user.click(screen.getByRole("button", { name: "Save token" }));

    await waitFor(() => expect(hfTokenStore.token).toBe("hf_pretendtoken123"));
    // Stored means the field is gone: the page states the fact, not the secret.
    expect(await screen.findByText(/token stored for huggingface.co/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Hugging Face token/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Forget it" }));
    await waitFor(() => expect(hfTokenStore.token).toBeNull());
  });

  it("builds a skill environment from the same page", async () => {
    const user = userEvent.setup();
    await openModelsPage();
    const envs = screen.getByRole("region", { name: "Skill environments" });

    await user.click(within(envs).getByRole("button", { name: /Build env skill:skills\/audio\/kokoro/ }));

    await waitFor(() => expect(installPosts).toEqual(["skill:skills/audio/kokoro"]));
  });
});
