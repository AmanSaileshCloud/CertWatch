import type { IntervalSeconds, MonitorInput, MonitorRecord } from "../../types";
import type { AlertFieldValues } from "./MonitorAlertFields";

/** Form state, validation and payload building for the add / edit website modal. */

export interface FormState extends AlertFieldValues {
  name: string;
  url: string;
  interval: IntervalSeconds;
  timeout: string;
  expectedStatus: string;
  keyword: string;
  groupId: number | null;
  /** Only used when no group is chosen; a group implies its own customer. */
  customerId: number | null;
}

export type FieldErrors = Partial<Record<keyof FormState, string>>;

export const INTERVALS: { value: IntervalSeconds; label: string }[] = [
  { value: 60, label: "Every 1 minute" },
  { value: 300, label: "Every 5 minutes" },
  { value: 600, label: "Every 10 minutes" },
];

export function initialState(monitor: MonitorRecord | undefined, defaultGroupId: number | null): FormState {
  return {
    name: monitor?.name ?? "",
    url: monitor?.url ?? "https://",
    interval: monitor?.interval_seconds ?? 300,
    timeout: String(monitor?.timeout_seconds ?? 10),
    expectedStatus: String(monitor?.expected_status ?? 200),
    keyword: monitor?.keyword ?? "",
    groupId: monitor ? monitor.group_id : defaultGroupId,
    customerId: monitor?.customer_id ?? null,
    alertsEnabled: monitor?.alerts_enabled ?? true,
    alertDelayMinutes: monitor?.alert_delay_minutes ?? null,
    reminderMinutes: monitor?.reminder_minutes ?? null,
  };
}

function urlError(raw: string): string | undefined {
  if (raw.length > 2000) return "URL is too long (max 2000 characters).";
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return "Enter a full URL, e.g. https://example.com";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return "Only http:// and https:// URLs are supported.";
  if (!parsed.hostname) return "The URL needs a host name.";
  if (parsed.username || parsed.password) return "Don't put credentials in the URL.";
  return undefined;
}

function intInRange(raw: string, min: number, max: number): boolean {
  const n = Number(raw);
  return raw.trim() !== "" && Number.isInteger(n) && n >= min && n <= max;
}

/** Mirrors the API's 422 rules so most mistakes are caught before submitting. */
export function validate(form: FormState): FieldErrors {
  const errors: FieldErrors = {};
  const name = form.name.trim();
  if (!name) errors.name = "Name is required.";
  else if (name.length > 200) errors.name = "Name is too long (max 200 characters).";
  errors.url = urlError(form.url.trim());
  if (!intInRange(form.timeout, 1, 60)) errors.timeout = "Timeout must be 1–60 seconds.";
  if (!intInRange(form.expectedStatus, 100, 599)) errors.expectedStatus = "Status must be 100–599.";
  if (form.keyword.trim().length > 200) errors.keyword = "Keyword is too long (max 200 characters).";
  return Object.fromEntries(Object.entries(errors).filter(([, v]) => v)) as FieldErrors;
}

export function toInput(form: FormState): MonitorInput {
  return {
    name: form.name.trim(),
    url: form.url.trim(),
    interval_seconds: form.interval,
    timeout_seconds: Number(form.timeout),
    expected_status: Number(form.expectedStatus),
    keyword: form.keyword.trim() || null,
    group_id: form.groupId,
    customer_id: form.groupId === null ? form.customerId : null, // a group implies its customer
    alerts_enabled: form.alertsEnabled,
    alert_delay_minutes: form.alertDelayMinutes,
    reminder_minutes: form.reminderMinutes,
  };
}

/** Only the fields that differ from the saved monitor. The customer counts only when no group is set. */
export function changedFields(monitor: MonitorRecord, next: MonitorInput): Partial<MonitorInput> {
  const current: Omit<MonitorInput, "customer_id"> = {
    name: monitor.name,
    url: monitor.url,
    interval_seconds: monitor.interval_seconds,
    timeout_seconds: monitor.timeout_seconds,
    expected_status: monitor.expected_status,
    keyword: monitor.keyword,
    group_id: monitor.group_id,
    alerts_enabled: monitor.alerts_enabled,
    alert_delay_minutes: monitor.alert_delay_minutes,
    reminder_minutes: monitor.reminder_minutes,
  };
  const keys = Object.keys(current) as (keyof typeof current)[];
  const patch: Partial<MonitorInput> = Object.fromEntries(
    keys.filter((k) => current[k] !== next[k]).map((k) => [k, next[k]]),
  );
  if (next.group_id === null && next.customer_id !== monitor.customer_id) patch.customer_id = next.customer_id;
  return patch;
}
