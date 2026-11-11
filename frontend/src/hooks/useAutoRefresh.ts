import { useEffect, useRef } from "react";

/**
 * Call `refresh` every `intervalMs` while the browser tab is visible, and once
 * immediately when the tab becomes visible again after being hidden.
 */
export function useAutoRefresh(refresh: () => void, intervalMs = 30_000): void {
  const latest = useRef(refresh);
  latest.current = refresh;

  useEffect(() => {
    const isVisible = () => document.visibilityState === "visible";
    const timer = window.setInterval(() => {
      if (isVisible()) latest.current();
    }, intervalMs);
    const onVisibility = () => {
      if (isVisible()) latest.current();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs]);
}
