import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "@/features/theme/ThemeProvider";
import { ThemeToggle } from "@/features/theme/ThemeToggle";
import { THEME_STORAGE_KEY, useTheme } from "@/features/theme/themeContext";

let systemDark = false;
const listeners = new Set<(event: MediaQueryListEvent) => void>();

beforeEach(() => {
  systemDark = false;
  listeners.clear();
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      media: query,
      get matches() {
        return query.includes("dark") ? systemDark : false;
      },
      addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
      removeEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
});

function Probe() {
  const { preference, theme } = useTheme();
  return (
    <p>
      {preference}/{theme}
    </p>
  );
}

function renderTheme() {
  return render(
    <ThemeProvider>
      <ThemeToggle />
      <Probe />
    </ThemeProvider>,
  );
}

describe("theme", () => {
  it("follows the operating system by default, including later changes", () => {
    systemDark = true;
    renderTheme();
    expect(screen.getByText("system/dark")).toBeInTheDocument();
    expect(document.documentElement).toHaveClass("dark");
  });

  it("switches theme from the picker, applies it to <html> and remembers it", async () => {
    const user = userEvent.setup();
    renderTheme();
    expect(document.documentElement).not.toHaveClass("dark");

    await user.click(screen.getByRole("button", { name: "Theme: system" }));
    const menu = screen.getByRole("menu", { name: "Theme" });
    expect(within(menu).getByRole("menuitemradio", { name: "System" })).toHaveAttribute("aria-checked", "true");
    await user.click(within(menu).getByRole("menuitemradio", { name: "Dark" }));

    expect(screen.getByText("dark/dark")).toBeInTheDocument();
    expect(document.documentElement).toHaveClass("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

    await user.click(screen.getByRole("button", { name: "Theme: dark" }));
    await user.click(screen.getByRole("menuitemradio", { name: "Light" }));
    expect(document.documentElement).not.toHaveClass("dark");
  });

  it("restores a saved preference", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    renderTheme();
    expect(screen.getByText("dark/dark")).toBeInTheDocument();
  });

  it("ignores an invalid saved value", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "purple");
    renderTheme();
    expect(screen.getByText("system/light")).toBeInTheDocument();
  });
});
