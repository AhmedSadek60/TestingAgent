import { useCallback, useEffect, useState } from "react";
import { readStored, THEME_KEY, writeStored } from "../lib/storage";

export type Theme = "system" | "light" | "dark";

function apply(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

export function useTheme(): { theme: Theme; setTheme: (theme: Theme) => void } {
  const [theme, setThemeState] = useState<Theme>(() => {
    const stored = readStored("local", THEME_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  });
  useEffect(() => apply(theme), [theme]);
  const setTheme = useCallback((value: Theme) => {
    setThemeState(value);
    writeStored("local", THEME_KEY, value === "system" ? null : value);
  }, []);
  return { theme, setTheme };
}
