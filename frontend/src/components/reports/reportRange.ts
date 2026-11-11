import type { ReportGranularity } from "../../types";

export type RangePreset = "last7" | "last30" | "thisMonth" | "lastMonth" | "last90" | "custom";

export const PRESETS: { value: RangePreset; label: string }[] = [
  { value: "last7", label: "Last 7 days" },
  { value: "last30", label: "Last 30 days" },
  { value: "thisMonth", label: "This month" },
  { value: "lastMonth", label: "Last month" },
  { value: "last90", label: "Last 90 days" },
  { value: "custom", label: "Custom…" },
];

export const GRANULARITIES: { value: ReportGranularity; label: string }[] = [
  { value: "day", label: "Daily" },
  { value: "week", label: "Weekly" },
  { value: "month", label: "Monthly" },
  { value: "total", label: "Whole range" },
];

/** YYYY-MM-DD for a local calendar date. */
export function isoDate(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function daysAgo(today: Date, days: number): Date {
  return new Date(today.getFullYear(), today.getMonth(), today.getDate() - days);
}

/** The [start, end] dates (inclusive) a preset stands for. */
export function presetRange(preset: Exclude<RangePreset, "custom">, today = new Date()): [string, string] {
  switch (preset) {
    case "last7":
      return [isoDate(daysAgo(today, 6)), isoDate(today)];
    case "last30":
      return [isoDate(daysAgo(today, 29)), isoDate(today)];
    case "last90":
      return [isoDate(daysAgo(today, 89)), isoDate(today)];
    case "thisMonth":
      return [isoDate(new Date(today.getFullYear(), today.getMonth(), 1)), isoDate(today)];
    case "lastMonth":
      return [
        isoDate(new Date(today.getFullYear(), today.getMonth() - 1, 1)),
        isoDate(new Date(today.getFullYear(), today.getMonth(), 0)),
      ];
  }
}

/** A sensible breakdown for a preset: daily up to a month, weekly beyond. */
export function defaultGranularity(preset: RangePreset): ReportGranularity {
  return preset === "last90" ? "week" : "day";
}
