export type Status = "ok" | "warning" | "expired" | "unreachable";

export interface DomainRecord {
  domain: string;
  host: string;
  port: number;
  status: Status;
  not_after: string | null;
  days_remaining: number | null;
  last_checked_at: string | null;
  last_error: string | null;
  last_alert_threshold: number | null;
  created_at: string;
  alerts_enabled: boolean;
  /** "direct" = probed by the central checker; "agent" = pushed by a remote agent. */
  source: "direct" | "agent";
  agent_id: string | null;
  customer_id: number | null;
  group_id: number | null;
}

export interface CheckSummary {
  checked_at: string;
  checked: number;
  alerts_sent: number;
  by_status: Record<string, number>;
  domains: unknown[];
  /** Domains actually TLS-probed; agent-managed ones are excluded. */
  probed: number;
  /** Agent-managed domains whose agent has stopped reporting. */
  stale_agents: number;
}

export interface User {
  username: string;
  role: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

export interface MessageResponse {
  status: string;
  message: string;
}

export interface CertInfo {
  issuer: string;
  subject: string;
  serial: string;
  sig_algorithm: string;
  key_type: string;
  key_bits: number | null;
  not_before: string | null;
  not_after: string | null;
  sans: string[];
}

export interface AdminUser {
  username: string;
  role: string;
}

export interface NewUser {
  username: string;
  password: string;
  role: "user" | "admin";
}

// ── Uptime monitoring + groups (see the API contract) ──────────────────────

export interface Customer {
  id: number;
  name: string;
  contact_email: string | null;
  created_at: string;
  group_count: number;
}

export type GroupHealth = "ok" | "warning" | "down" | "empty";

export interface GroupSummary {
  id: number;
  customer_id: number;
  customer_name: string;
  name: string;
  created_at: string;
  ssl_count: number;
  website_count: number;
  health: GroupHealth;
  problems: number;
}

export interface GroupDetail extends GroupSummary {
  domains: DomainRecord[];
  monitors: MonitorRecord[];
}

export type MonitorStatus = "up" | "down" | "paused" | "unknown";
export type IntervalSeconds = 60 | 300 | 600;

export interface MonitorRecord {
  id: number;
  name: string;
  url: string;
  type: "http";
  interval_seconds: IntervalSeconds;
  timeout_seconds: number;
  expected_status: number;
  keyword: string | null;
  customer_id: number | null;
  customer_name: string | null;
  group_id: number | null;
  group_name: string | null;
  is_paused: boolean;
  status: MonitorStatus;
  last_checked_at: string | null;
  last_status_code: number | null;
  last_response_ms: number | null;
  last_error: string | null;
  /** Percent (0–100) over the last 24h; null when there were no checks. */
  uptime_24h: number | null;
  /** When the current outage started (only while status is "down"). */
  down_since: string | null;
  created_at: string;
  alerts_enabled: boolean;
  /** null = use the global default (see effective_*). */
  reminder_minutes: ReminderMinutes | null;
  alert_delay_minutes: AlertDelayMinutes | null;
  effective_reminder_minutes: ReminderMinutes;
  effective_alert_delay_minutes: AlertDelayMinutes;
}

/** Repeat "still down" emails every N minutes; 0 = no reminders. */
export type ReminderMinutes = 0 | 15 | 30 | 60 | 120;
/** Only alert once a site has been down this many minutes. */
export type AlertDelayMinutes = 0 | 5 | 10 | 15 | 30;

export interface MonitorInput {
  name: string;
  url: string;
  interval_seconds: IntervalSeconds;
  timeout_seconds: number;
  expected_status: number;
  keyword: string | null;
  group_id: number | null;
  customer_id: number | null;
  alerts_enabled: boolean;
  reminder_minutes: ReminderMinutes | null;
  alert_delay_minutes: AlertDelayMinutes | null;
}

export interface CheckRecord {
  checked_at: string;
  is_up: boolean;
  status_code: number | null;
  response_ms: number | null;
  error: string | null;
  region: string;
}

export type CloseReason = "recovered" | "paused" | "edited";

export interface IncidentRecord {
  id: number;
  monitor_id: number;
  started_at: string;
  resolved_at: string | null;
  duration_seconds: number | null;
  reason: string | null;
  close_reason: CloseReason | null;
  down_alert_sent_at: string | null;
  recovery_alert_sent_at: string | null;
  last_reminder_sent_at: string | null;
  reminder_count: number;
}

export interface AlertRule {
  id: number;
  target: string;
  channel: "email";
  customer_id: number | null;
  monitor_id: number | null;
  on_down: boolean;
  on_recovery: boolean;
  enabled: boolean;
  created_at: string;
}

export interface AlertSettings {
  default_recipients: string[];
  reminder_minutes: ReminderMinutes;
  alert_delay_minutes: AlertDelayMinutes;
  notifier: "console" | "sns_ses";
  sender: string | null;
}

export interface AddMemberInput {
  target: string;
  add_ssl: boolean;
  add_website: boolean;
  interval_seconds: IntervalSeconds;
}

export interface AddMemberResult {
  domain: DomainRecord | null;
  monitor: MonitorRecord | null;
  messages: string[];
}

// ── Admin settings: alert recipients + SES email delivery ───────────────────

export type EmailTransport = "iam_role" | "smtp";

export interface AlertSettingsAdmin {
  sender: string | null;
  default_recipients: string[];
  reminder_minutes: ReminderMinutes;
  alert_delay_minutes: AlertDelayMinutes;
  /** true = value saved in the UI; false = coming from the server environment. */
  saved: { sender: boolean; default_recipients: boolean; reminder_minutes: boolean; alert_delay_minutes: boolean };
  env_defaults: {
    sender: string | null;
    default_recipients: string[];
    reminder_minutes: ReminderMinutes;
    alert_delay_minutes: AlertDelayMinutes;
  };
  transport: EmailTransport;
  aws_region: string;
  smtp_host: string | null;
  smtp_port: number;
  smtp_username: string | null;
  smtp_password_set: boolean;
  encryption_available: boolean;
  updated_at: string | null;
  updated_by: string | null;
}

/** PUT body: omit a field = unchanged; null = clear (fall back to env / remove password). */
export interface AlertSettingsUpdate {
  sender?: string | null;
  default_recipients?: string[] | null;
  reminder_minutes?: ReminderMinutes | null;
  alert_delay_minutes?: AlertDelayMinutes | null;
  transport?: EmailTransport;
  smtp_host?: string | null;
  smtp_port?: number;
  smtp_username?: string | null;
  smtp_password?: string | null;
}
