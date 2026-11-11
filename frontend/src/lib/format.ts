const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now", "2 min ago", "3 h ago", "5 d ago" — or "never" for null. */
export function formatRelativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "never";
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 0) return "in the future";
  if (seconds < 45) return "just now";
  if (seconds < HOUR) return `${Math.max(1, Math.round(seconds / MINUTE))} min ago`;
  if (seconds < DAY) return `${Math.round(seconds / HOUR)} h ago`;
  return `${Math.round(seconds / DAY)} d ago`;
}

/** 580 → "9m 40s", 3720 → "1h 2m", 90000 → "1d 1h". */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  if (s < MINUTE) return `${s}s`;
  if (s < HOUR) return `${Math.floor(s / MINUTE)}m ${s % MINUTE}s`;
  if (s < DAY) return `${Math.floor(s / HOUR)}h ${Math.floor((s % HOUR) / MINUTE)}m`;
  return `${Math.floor(s / DAY)}d ${Math.floor((s % DAY) / HOUR)}h`;
}

export function formatMs(ms: number | null): string {
  if (ms === null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms`;
}

export function formatPercent(value: number | null): string {
  if (value === null) return "—";
  return `${value.toFixed(value === 100 ? 0 : 2)}%`;
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatClock(time: number): string {
  return new Date(time).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export type UptimeLevel = "good" | "fair" | "poor" | "none";

/** ≥ 99.9 good (green), ≥ 99 fair (amber), else poor (red). */
export function uptimeLevel(value: number | null): UptimeLevel {
  if (value === null) return "none";
  if (value >= 99.9) return "good";
  if (value >= 99) return "fair";
  return "poor";
}

/** "1 domain", "3 domains". */
export function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}
