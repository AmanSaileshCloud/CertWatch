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
}

export interface CheckSummary {
  checked_at: string;
  checked: number;
  alerts_sent: number;
  by_status: Record<string, number>;
  domains: unknown[];
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
