import type {
  AdminUser,
  CertInfo,
  CheckSummary,
  DomainRecord,
  MessageResponse,
  NewUser,
  TokenResponse,
  User,
} from "./types";

// Empty string = same-origin (Nginx / dev proxy). In production VITE_API_URL is
// the API base injected at build time.
const BASE = (import.meta.env.VITE_API_URL ?? import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
const TOKEN_KEY = "certwatch_token";

export class ApiError extends Error {}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/** Extract a human-readable message from FastAPI's varied error shapes. */
async function errorMessage(res: Response): Promise<string> {
  try {
    const data = await res.json();
    const detail = data?.detail ?? data?.error;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((d: { msg?: string }) => d.msg ?? String(d)).join("; ");
    }
  } catch {
    /* fall through */
  }
  return `${res.status} ${res.statusText}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...init,
    });
  } catch {
    throw new ApiError("Cannot reach the API. Is it running on :8000?");
  }

  if (res.status === 401) {
    // Token missing/expired — drop it and let the app fall back to login.
    clearToken();
    window.dispatchEvent(new Event("auth:unauthorized"));
    throw new ApiError(await errorMessage(res));
  }
  if (!res.ok) throw new ApiError(await errorMessage(res));
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  login: (username: string, password: string) =>
    request<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  me: () => request<User>("/auth/me"),

  changePassword: (current_password: string, new_password: string) =>
    request<MessageResponse>("/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),

  getCertDetails: (domainKey: string) =>
    request<CertInfo>(`/domains/${encodeURIComponent(domainKey)}/cert`),

  listUsers: () => request<AdminUser[]>("/admin/users"),

  addUser: (payload: NewUser) =>
    request<AdminUser>("/admin/users", { method: "POST", body: JSON.stringify(payload) }),

  deleteUser: (username: string) =>
    request<void>(`/admin/users/${encodeURIComponent(username)}`, { method: "DELETE" }),

  listDomains: () => request<DomainRecord[]>("/domains"),

  addDomain: (domain: string, port: number) =>
    request<DomainRecord>("/domains", {
      method: "POST",
      body: JSON.stringify({ domain, port }),
    }),

  updateDomain: (
    domainKey: string,
    payload: { notify_emails?: string[]; alerts_enabled?: boolean },
  ) =>
    request<DomainRecord>(`/domains/${encodeURIComponent(domainKey)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteDomain: (domainKey: string) =>
    request<void>(`/domains/${encodeURIComponent(domainKey)}`, { method: "DELETE" }),

  bulkAddDomains: (domains: Array<{ domain: string; port: number }>) =>
    request<{ added: DomainRecord[]; failed: Array<{ domain: string; error: string }> }>(
      "/domains/bulk",
      { method: "POST", body: JSON.stringify({ domains }) },
    ),

  testAlert: (domainKey: string) =>
    request<MessageResponse>(`/domains/${encodeURIComponent(domainKey)}/test-alert`, {
      method: "POST",
    }),

  /** Fetch the rendered status report PDF (admin-only) for local download. */
  downloadReport: async (): Promise<Blob> => {
    const token = getToken();
    let res: Response;
    try {
      res = await fetch(`${BASE}/checks/digest`, {
        headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      });
    } catch {
      throw new ApiError("Cannot reach the API. Is it running on :8000?");
    }
    if (res.status === 401) {
      clearToken();
      window.dispatchEvent(new Event("auth:unauthorized"));
      throw new ApiError(await errorMessage(res));
    }
    if (!res.ok) throw new ApiError(await errorMessage(res));
    return res.blob();
  },

  runChecks: () => request<CheckSummary>("/checks/run", { method: "POST" }),
};
