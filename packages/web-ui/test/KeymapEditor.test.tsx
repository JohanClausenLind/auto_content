import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useMemo } from "react";
import { describe, expect, it, vi } from "vitest";
import { KeymapEditor, KeymapProvider, loadKeymapState, type KeyAction } from "../src/index";

function Harness({ fire }: { fire: (id: string) => void }) {
  const actions = useMemo<KeyAction[]>(
    () => [
      { id: "create", title: "Create", group: "General", defaultBinding: ["c"], run: () => fire("create") },
      { id: "theme", title: "Toggle theme", group: "General", defaultBinding: ["t"], run: () => fire("theme") },
      { id: "palette", title: "Command palette", group: "General", defaultBinding: ["Mod+k"], locked: true, run: () => fire("palette") },
      { id: "inbox", title: "Go to Inbox", group: "Navigate", defaultBinding: ["g", "i"], run: () => fire("inbox") },
    ],
    [fire],
  );
  return (
    <KeymapProvider actions={actions}>
      <input aria-label="note" />
      <KeymapEditor commitDelayMs={10} />
    </KeymapProvider>
  );
}

const setup = () => {
  const fire = vi.fn();
  const user = userEvent.setup();
  render(<Harness fire={fire} />);
  return { fire, user };
};

describe("keymap dispatch", () => {
  it("fires a single-chord binding", async () => {
    const { fire, user } = setup();
    await user.keyboard("c");
    expect(fire).toHaveBeenCalledWith("create");
  });

  it("fires a two-chord sequence and not the prefix on its own", async () => {
    const { fire, user } = setup();
    await user.keyboard("g");
    expect(fire).not.toHaveBeenCalled();
    await user.keyboard("i");
    expect(fire).toHaveBeenCalledExactlyOnceWith("inbox");
  });

  it("abandons a sequence when the second key matches nothing", async () => {
    const { fire, user } = setup();
    await user.keyboard("gz");
    expect(fire).not.toHaveBeenCalled();
  });

  it("ignores modifier-free shortcuts while a field has focus, but still takes Mod chords", async () => {
    const { fire, user } = setup();
    const field = screen.getByLabelText("note");
    await user.type(field, "ct");
    expect(fire).not.toHaveBeenCalled();
    expect(field).toHaveValue("ct");

    await user.keyboard("{Control>}k{/Control}");
    expect(fire).toHaveBeenCalledExactlyOnceWith("palette");
  });
});

describe("KeymapEditor", () => {
  it("records a new binding, persists it, and dispatches on it", async () => {
    const { fire, user } = setup();
    await user.click(screen.getByRole("button", { name: "Record a shortcut for Create" }));
    await user.keyboard("x");

    await waitFor(() => expect(screen.getByRole("button", { name: "Record a shortcut for Create" })).toBeInTheDocument());
    await waitFor(() => expect(loadKeymapState()?.overrides["create"]).toEqual(["x"]));

    await user.keyboard("x");
    expect(fire).toHaveBeenCalledWith("create");
    // The old default is no longer live.
    fire.mockClear();
    await user.keyboard("c");
    expect(fire).not.toHaveBeenCalled();
  });

  it("records a two-chord sequence", async () => {
    const { fire, user } = setup();
    await user.click(screen.getByRole("button", { name: "Record a shortcut for Toggle theme" }));
    await user.keyboard("gt");
    await waitFor(() => expect(loadKeymapState()?.overrides["theme"]).toEqual(["g", "t"]));

    await user.keyboard("gt");
    expect(fire).toHaveBeenCalledWith("theme");
  });

  it("does not fire the action it is binding while recording it", async () => {
    const { fire, user } = setup();
    await user.click(screen.getByRole("button", { name: "Record a shortcut for Toggle theme" }));
    await user.keyboard("c"); // 'c' is Create's binding
    await waitFor(() => expect(loadKeymapState()?.overrides["theme"]).toEqual(["c"]));
    expect(fire).not.toHaveBeenCalled();
  });

  it("abandons recording on Escape and leaves the binding alone", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Record a shortcut for Create" }));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByRole("button", { name: "Record a shortcut for Create" })).toBeInTheDocument());
    expect(loadKeymapState()?.overrides["create"]).toBeUndefined();
  });

  it("flags a duplicate rather than refusing it", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Record a shortcut for Toggle theme" }));
    await user.keyboard("c");
    expect(await screen.findByText("Same as Create")).toBeInTheDocument();
    expect(await screen.findByText("Same as Toggle theme")).toBeInTheDocument();
  });

  it("flags a binding that would shadow a longer sequence", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Record a shortcut for Create" }));
    await user.keyboard("g");
    // One chord then a pause commits it, and now 'g' can never reach 'g i'.
    expect(await screen.findByText("Shadows Go to Inbox")).toBeInTheDocument();
  });

  it("clears a binding and then restores the default", async () => {
    const { fire, user } = setup();
    await user.click(screen.getByRole("button", { name: "Unbind Create" }));
    await waitFor(() => expect(loadKeymapState()?.overrides["create"]).toBeNull());
    await user.keyboard("c");
    expect(fire).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Reset Create to its default" }));
    await waitFor(() => expect(loadKeymapState()?.overrides["create"]).toBeUndefined());
    await user.keyboard("c");
    expect(fire).toHaveBeenCalledWith("create");
  });

  it("resets everything at once", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Record a shortcut for Create" }));
    await user.keyboard("x");
    await waitFor(() => expect(loadKeymapState()?.overrides["create"]).toEqual(["x"]));

    await user.click(screen.getByRole("button", { name: "Reset all to defaults" }));
    await waitFor(() => expect(loadKeymapState()?.overrides).toEqual({}));
  });

  it("offers no way to rebind a locked action", () => {
    setup();
    expect(screen.queryByRole("button", { name: "Record a shortcut for Command palette" })).not.toBeInTheDocument();
    expect(screen.getByText("Fixed")).toBeInTheDocument();
  });
});
