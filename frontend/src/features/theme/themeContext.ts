import { createContext, useContext } from "react";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export interface ThemeContextValue {
  /** What the user chose. "system" follows the operating system. */
  preference: ThemePreference;
  /** The theme in effect right now. */
  theme: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
}

export const THEME_STORAGE_KEY = "opspilot.theme";

export const ThemeContext = createContext<ThemeContextValue | null>(null);

export function useTheme(): ThemeContextValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside <ThemeProvider>");
  return value;
}
