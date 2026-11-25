import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Alert } from "@/components/ui/Alert";
import { Avatar } from "@/components/ui/Avatar";
import { Badge } from "@/components/ui/Badge";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Pagination } from "@/components/ui/Pagination";
import { Spinner } from "@/components/ui/Spinner";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import { Tabs } from "@/components/ui/Tabs";
import { ToastProvider } from "@/components/ui/Toast";
import { useToast } from "@/components/ui/toastContext";
import { initials } from "@/utils/initials";

describe("feedback components", () => {
  it("announces errors assertively and other alerts politely", () => {
    render(
      <>
        <Alert tone="error">Broken</Alert>
        <Alert tone="success">Saved</Alert>
      </>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Broken");
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
  });

  it("renders status with a text label, never colour alone", () => {
    render(<StatusIndicator tone="success" label="Active" />);
    expect(screen.getByText("Active").className).toContain("text-success-text");
  });

  it("renders badges, spinners, empty and error states", async () => {
    const onRetry = vi.fn();
    render(
      <>
        <Badge tone="info">Beta</Badge>
        <Spinner label="Loading users" />
        <EmptyState title="No users found" description="Try another search." />
        <ErrorState message="The server is unavailable." onRetry={onRetry} />
      </>,
    );
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Loading users");
    expect(screen.getByText("No users found")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("The server is unavailable.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("builds avatar initials from a name and hides the avatar from assistive technology", () => {
    expect(initials("Ada Lovelace")).toBe("AL");
    expect(initials("  grace  ")).toBe("G");
    expect(initials("Jean Baptiste Joseph Fourier")).toBe("JF");
    expect(initials("")).toBe("?");
    const { container } = render(<Avatar name="Ada Lovelace" />);
    expect(container.firstChild).toHaveAttribute("aria-hidden", "true");
  });
});

function ToastButton() {
  const toast = useToast();
  return (
    <>
      <button type="button" onClick={() => toast.notify({ title: "User activated", durationMs: 1000 })}>
        Notify
      </button>
      <button type="button" onClick={() => toast.notify({ tone: "error", title: "Could not save" })}>
        Fail
      </button>
    </>
  );
}

describe("Toast", () => {
  it("shows notifications in live regions and dismisses them", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <ToastProvider>
        <ToastButton />
      </ToastProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Notify" }));
    const polite = screen.getByRole("list", { name: "Notifications" });
    expect(polite).toHaveAttribute("aria-live", "polite");
    expect(within(polite).getByText("User activated")).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(1100);
    });
    expect(screen.queryByText("User activated")).not.toBeInTheDocument();

    // Errors stay until dismissed and are announced assertively.
    await user.click(screen.getByRole("button", { name: "Fail" }));
    const assertive = screen.getByRole("list", { name: "Error notifications" });
    expect(assertive).toHaveAttribute("aria-live", "assertive");
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    await user.click(within(assertive).getByRole("button", { name: "Dismiss notification" }));
    expect(screen.queryByText("Could not save")).not.toBeInTheDocument();
    vi.useRealTimers();
  });
});

interface Row {
  id: string;
  name: string;
}
const columns: DataTableColumn<Row>[] = [
  { id: "name", header: "Name", cell: (row) => row.name },
  { id: "id", header: "Id", cell: (row) => row.id, hideBelow: "md" },
];

describe("DataTable", () => {
  it("shows a loading state, then rows", () => {
    const { rerender } = render(<DataTable caption="People" columns={columns} rows={undefined} getRowId={(r) => r.id} loading loadingLabel="Loading people" empty={{ title: "Nobody" }} />);
    expect(screen.getByRole("status", { name: "Loading people" })).toBeInTheDocument();
    rerender(<DataTable caption="People" columns={columns} rows={[{ id: "1", name: "Ada" }]} getRowId={(r) => r.id} empty={{ title: "Nobody" }} />);
    const table = screen.getByRole("table", { name: "People" });
    expect(within(table).getByRole("columnheader", { name: "Id" }).className).toContain("hidden md:table-cell");
    expect(within(table).getByRole("cell", { name: "Ada" })).toBeInTheDocument();
  });

  it("shows empty and error states", async () => {
    const onRetry = vi.fn();
    const { rerender } = render(<DataTable caption="People" columns={columns} rows={[]} getRowId={(r) => r.id} empty={{ title: "Nobody here" }} />);
    expect(screen.getByText("Nobody here")).toBeInTheDocument();
    rerender(<DataTable caption="People" columns={columns} rows={undefined} getRowId={(r) => r.id} error="Failed to load." onRetry={onRetry} empty={{ title: "Nobody" }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Failed to load.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });
});

describe("Pagination", () => {
  it("summarises the range and moves between pages", async () => {
    const onPage = vi.fn();
    const { rerender } = render(<Pagination page={0} pageSize={20} total={45} onPage={onPage} itemLabel="Users" />);
    expect(screen.getByRole("navigation", { name: "Users pagination" })).toHaveTextContent("Showing 1–20 of 45");
    expect(screen.getByRole("button", { name: /Previous/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(onPage).toHaveBeenCalledWith(1);
    rerender(<Pagination page={2} pageSize={20} total={45} onPage={onPage} />);
    expect(screen.getByText("Showing 41–45 of 45")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();
  });

  it("renders nothing without results", () => {
    const { container } = render(<Pagination page={0} pageSize={20} total={0} onPage={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("Tabs", () => {
  it("follows the tabs pattern with arrow keys", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    render(
      <Tabs
        label="Sections"
        value="a"
        onValueChange={onValueChange}
        items={[
          { id: "a", label: "Alpha", content: <p>Alpha content</p> },
          { id: "b", label: "Beta", content: <p>Beta content</p> },
        ]}
      />,
    );
    const alpha = screen.getByRole("tab", { name: "Alpha" });
    expect(alpha).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Beta" })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tabpanel", { name: "Alpha" })).toHaveTextContent("Alpha content");
    alpha.focus();
    await user.keyboard("{ArrowRight}");
    expect(onValueChange).toHaveBeenCalledWith("b");
  });
});
