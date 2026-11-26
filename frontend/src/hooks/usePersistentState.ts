import { useCallback, useState } from "react";

/**
 * useState backed by localStorage, for harmless UI preferences only (sidebar
 * collapsed, theme). Never store tokens or user data with this.
 */
export function usePersistentBoolean(key: string, initial: boolean): [boolean, (value: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => {
    try {
      const stored = window.localStorage.getItem(key);
      return stored === null ? initial : stored === "true";
    } catch {
      return initial;
    }
  });
  const update = useCallback(
    (next: boolean) => {
      setValue(next);
      try {
        window.localStorage.setItem(key, String(next));
      } catch {
        // Not persisted; still applies for this visit.
      }
    },
    [key],
  );
  return [value, update];
}
