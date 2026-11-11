import type {
  AddMemberInput,
  AddMemberResult,
  AdminUser,
  CheckRecord,
  Customer,
  GroupDetail,
  GroupSummary,
  IncidentRecord,
  MonitorInput,
  MonitorRecord,
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

  addDomain: (domain: string, port: number, agent_id?: string) =>
    request<DomainRecord>("/domains", {
      method: "POST",
      body: JSON.stringify({ domain, port, ...(agent_id ? { agent_id } : {}) }),
    }),

  updateDomain: (
    domainKey: string,
    payload: { alerts_enabled?: boolean; group_id?: number | null },
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

  // ── Website monitors ──
  listMonitors: (filter: { group_id?: number; customer_id?: number } = {}) =>
    request<MonitorRecord[]>(`/monitors${query(filter)}`),
  getMonitor: (id: number) => request<MonitorRecord>(`/monitors/${id}`),
  addMonitor: (payload: MonitorInput) =>
    request<MonitorRecord>("/monitors", { method: "POST", body: JSON.stringify(payload) }),
  updateMonitor: (id: number, payload: Partial<MonitorInput>) =>
    request<MonitorRecord>(`/monitors/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteMonitor: (id: number) => request<void>(`/monitors/${id}`, { method: "DELETE" }),
  pauseMonitor: (id: number) => request<MonitorRecord>(`/monitors/${id}/pause`, { method: "POST" }),
  resumeMonitor: (id: number) =>
    request<MonitorRecord>(`/monitors/${id}/resume`, { method: "POST" }),
  listChecks: (id: number, limit = 50) =>
    request<CheckRecord[]>(`/monitors/${id}/checks${query({ limit })}`),
  getSeries: (id: number, hours = 24) =>
    request<CheckRecord[]>(`/monitors/${id}/series${query({ hours })}`),
  listMonitorIncidents: (id: number, limit = 50) =>
    request<IncidentRecord[]>(`/monitors/${id}/incidents${query({ limit })}`),

  // ── Customers ──
  listCustomers: () => request<Customer[]>("/customers"),
  addCustomer: (name: string, contact_email: string | null) =>
    request<Customer>("/customers", { method: "POST", body: JSON.stringify({ name, contact_email }) }),
  updateCustomer: (id: number, payload: { name?: string; contact_email?: string | null }) =>
    request<Customer>(`/customers/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteCustomer: (id: number) => request<void>(`/customers/${id}`, { method: "DELETE" }),

  // ── Groups ──
  listGroups: (customer_id?: number) =>
    request<GroupSummary[]>(`/groups${query({ customer_id })}`),
  getGroup: (id: number) => request<GroupDetail>(`/groups/${id}`),
  addGroup: (customer_id: number, name: string) =>
    request<GroupSummary>("/groups", { method: "POST", body: JSON.stringify({ customer_id, name }) }),
  renameGroup: (id: number, name: string) =>
    request<GroupSummary>(`/groups/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  deleteGroup: (id: number) => request<void>(`/groups/${id}`, { method: "DELETE" }),
  addGroupMember: (groupId: number, payload: AddMemberInput) =>
    request<AddMemberResult>(`/groups/${groupId}/members`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};

/** Build a query string from defined values only: {a: 1, b: undefined} → "?a=1". */
function query(params: Record<string, string | number | undefined>): string {
  const pairs = Object.entries(params).filter(([, v]) => v !== undefined) as [string, string | number][];
  if (pairs.length === 0) return "";
  return "?" + new URLSearchParams(pairs.map(([k, v]) => [k, String(v)])).toString();
}
