import type { AlertDelayMinutes, ReminderMinutes } from "../types";

export const DELAY_CHOICES: AlertDelayMinutes[] = [0, 5, 10, 15, 30];
export const REMINDER_CHOICES: ReminderMinutes[] = [0, 15, 30, 60, 120];

/** 5 → "5 min", 60 → "1 hour", 120 → "2 hours". */
export function formatMinutes(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const hours = minutes / 60;
  return `${hours} hour${hours === 1 ? "" : "s"}`;
}

export function delayLabel(minutes: AlertDelayMinutes): string {
  return minutes === 0 ? "Immediately" : formatMinutes(minutes);
}

export function reminderLabel(minutes: ReminderMinutes): string {
  return minutes === 0 ? "Never" : formatMinutes(minutes);
}

/** "Alert after 5 min · remind every 30 min" */
export function timingSummary(delay: AlertDelayMinutes, reminder: ReminderMinutes): string {
  const first = delay === 0 ? "Alert immediately" : `Alert after ${formatMinutes(delay)}`;
  const repeat = reminder === 0 ? "no reminders" : `remind every ${formatMinutes(reminder)}`;
  return `${first} · ${repeat}`;
}

/** Deliberately loose (x@y.z); the API does the strict check. */
export function isValidEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}
