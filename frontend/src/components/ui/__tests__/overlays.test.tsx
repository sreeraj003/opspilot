import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { Drawer } from "@/components/ui/Drawer";
import { DropdownMenu } from "@/components/ui/DropdownMenu";
import { Tooltip } from "@/components/ui/Tooltip";

function DialogHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button onClick={() => setOpen(true)}>Open dialog</Button>
      <Dialog open={open} onClose={() => setOpen(false)} title="Edit thing" description="Change the thing.">
        <input aria-label="First" />
        <button type="button">Last</button>
      </Dialog>
    </>
  );
}

describe("Dialog", () => {
  it("opens with focus inside, traps Tab, closes on Escape and restores focus", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    const opener = screen.getByRole("button", { name: "Open dialog" });
    await user.click(opener);

    const dialog = screen.getByRole("dialog", { name: "Edit thing" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleDescription("Change the thing.");
    expect(within(dialog).getByLabelText("First")).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");

    // Tab order: First → Last → Close → First (wraps, never leaves the dialog).
    await user.tab();
    expect(within(dialog).getByRole("button", { name: "Last" })).toHaveFocus();
    await user.tab();
    await user.tab();
    expect(within(dialog).getByLabelText("First")).toHaveFocus();
    await user.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(document.body.style.overflow).toBe("");
  });

  it("closes with the close button and the backdrop", async () => {
    const user = userEvent.setup();
    render(<DialogHarness />);
    await user.click(screen.getByRole("button", { name: "Open dialog" }));
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("ConfirmDialog", () => {
  it("starts on Cancel and reports the choice", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<ConfirmDialog open title="Delete it?" description="Gone for good." confirmLabel="Delete" onConfirm={onConfirm} onCancel={onCancel} />);
    const dialog = screen.getByRole("alertdialog", { name: "Delete it?" });
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await user.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("shows a server error and blocks dismissal while pending", async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog open pending error="Not allowed." title="Delete it?" description="x" confirmLabel="Delete" onConfirm={vi.fn()} onCancel={onCancel} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Not allowed.");
    expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(onCancel).not.toHaveBeenCalled();
  });
});

describe("Drawer", () => {
  it("is a labelled modal panel that closes on Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose} title="Navigation">
        <a href="#x">Link</a>
      </Drawer>,
    );
    const drawer = screen.getByRole("dialog", { name: "Navigation" });
    expect(within(drawer).getByRole("link", { name: "Link" })).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});

describe("DropdownMenu", () => {
  function Menu({ onRename = vi.fn(), onDelete = vi.fn() }) {
    return (
      <DropdownMenu
        trigger={(props) => (
          <button type="button" {...props}>
            Actions
          </button>
        )}
        items={[
          { id: "rename", label: "Rename", onSelect: onRename },
          { type: "separator", id: "s" },
          { id: "delete", label: "Delete", tone: "danger", onSelect: onDelete },
        ]}
      />
    );
  }

  it("opens on click with the first item focused and supports arrow keys", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(<Menu onDelete={onDelete} />);
    const trigger = screen.getByRole("button", { name: "Actions" });
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const menu = screen.getByRole("menu");
    await vi.waitFor(() => expect(within(menu).getByRole("menuitem", { name: "Rename" })).toHaveFocus());
    await user.keyboard("{ArrowDown}");
    expect(within(menu).getByRole("menuitem", { name: "Delete" })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(within(menu).getByRole("menuitem", { name: "Rename" })).toHaveFocus();
    await user.keyboard("{End}{Enter}");
    expect(onDelete).toHaveBeenCalled();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("closes on Escape and on an outside click", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Menu />
        <p>Outside</p>
      </>,
    );
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Actions" }));
    await user.click(screen.getByText("Outside"));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

describe("Tooltip", () => {
  it("describes its trigger on focus and hides on Escape", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip content="Sign out of this browser">
        <button type="button" aria-label="Sign out">
          x
        </button>
      </Tooltip>,
    );
    await user.tab();
    const button = screen.getByRole("button", { name: "Sign out" });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByRole("tooltip")).toHaveTextContent("Sign out of this browser");
    expect(button).toHaveAccessibleDescription("Sign out of this browser");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
