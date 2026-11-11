import { useEffect, useRef } from "react";

/** Call `onEscape` when the Escape key is pressed anywhere, while `enabled` is true. */
export function useEscapeKey(onEscape: () => void, enabled = true): void {
  const latest = useRef(onEscape);
  latest.current = onEscape;

  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") latest.current();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}
