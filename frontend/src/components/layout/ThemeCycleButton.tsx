import type { ThemePreference } from "../../hooks/useTheme";

const LABELS: Record<ThemePreference, string> = {
  dark: "Dark mode",
  light: "Light mode",
  auto: "Auto (OS)",
};

export function ThemeCycleButton({ preference, onCycle }: { preference: ThemePreference; onCycle: () => void }) {
  return (
    <button
      className="theme-cycle btn btn--ghost"
      onClick={onCycle}
      title={`${LABELS[preference]} — click to change`}
      aria-label="Cycle theme"
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {preference === "dark" && <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />}
        {preference === "light" && (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
          </>
        )}
        {preference === "auto" && (
          <>
            <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
            <line x1="8" y1="21" x2="16" y2="21" />
            <line x1="12" y1="17" x2="12" y2="21" />
          </>
        )}
      </svg>
    </button>
  );
}
