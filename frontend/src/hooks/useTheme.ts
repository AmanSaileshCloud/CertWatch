import { useEffect, useState } from "react";

export type ThemePreference = "dark" | "light" | "auto";
export type ResolvedTheme = "dark" | "light";

const KEY = "certwatch_theme";

function resolve(pref: ThemePreference): ResolvedTheme {
  if (pref === "auto") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return pref;
}

export function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>(
    () => (localStorage.getItem(KEY) as ThemePreference) ?? "dark",
  );

  const theme = resolve(preference);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(KEY, preference);
  }, [theme, preference]);

  // Re-apply when OS preference changes while in auto mode
  useEffect(() => {
    if (preference !== "auto") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      document.documentElement.setAttribute("data-theme", mq.matches ? "dark" : "light");
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [preference]);

  function cycle() {
    const next: ThemePreference =
      preference === "dark" ? "light" : preference === "light" ? "auto" : "dark";
    setPreference(next);
  }

  return { theme, preference, cycle };
}
