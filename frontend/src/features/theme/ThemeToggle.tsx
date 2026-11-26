import { Monitor, Moon, Sun } from "lucide-react";

import { buttonClasses } from "@/components/ui/buttonStyles";
import { DropdownMenu } from "@/components/ui/DropdownMenu";
import { useTheme, type ThemePreference } from "@/features/theme/themeContext";

const OPTIONS: { value: ThemePreference; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];

/** Theme picker: light, dark, or follow the operating system. */
export function ThemeToggle() {
  const { preference, theme, setPreference } = useTheme();
  const Icon = theme === "dark" ? Moon : Sun;
  return (
    <DropdownMenu
      label="Theme"
      trigger={(props) => (
        <button type="button" {...props} aria-label={`Theme: ${preference}`} className={buttonClasses({ variant: "ghost", size: "icon" })}>
          <Icon aria-hidden="true" className="h-4 w-4" />
        </button>
      )}
      items={OPTIONS.map(({ value, label, icon: OptionIcon }) => ({
        id: value,
        label,
        icon: <OptionIcon aria-hidden="true" className="h-4 w-4" />,
        checked: preference === value,
        onSelect: () => setPreference(value),
      }))}
      className="w-auto"
    />
  );
}
