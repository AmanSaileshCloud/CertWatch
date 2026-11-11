import { useEffect, useState, type FormEvent } from "react";
import { motion } from "motion/react";
import { api, ApiError } from "../../api";
import type { Customer, IntervalSeconds, MonitorInput, MonitorRecord } from "../../types";
import { useEscapeKey } from "../../hooks/useEscapeKey";
import { GroupPicker } from "../groups/GroupPicker";

type Props = {
  /** Present = edit mode (PATCH only the changed fields); absent = create. */
  monitor?: MonitorRecord;
  /** Preselected group when creating (e.g. from a group page). */
  defaultGroupId?: number | null;
  onClose: () => void;
  onSaved: (monitor: MonitorRecord) => void;
};

interface FormState {
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

type FieldErrors = Partial<Record<keyof FormState, string>>;

const INTERVALS: { value: IntervalSeconds; label: string }[] = [
  { value: 60, label: "Every 1 minute" },
  { value: 300, label: "Every 5 minutes" },
  { value: 600, label: "Every 10 minutes" },
];

function initialState(monitor: MonitorRecord | undefined, defaultGroupId: number | null): FormState {
  return {
    name: monitor?.name ?? "",
    url: monitor?.url ?? "https://",
    interval: monitor?.interval_seconds ?? 300,
    timeout: String(monitor?.timeout_seconds ?? 10),
    expectedStatus: String(monitor?.expected_status ?? 200),
    keyword: monitor?.keyword ?? "",
    groupId: monitor ? monitor.group_id : defaultGroupId,
    customerId: monitor?.customer_id ?? null,
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
function validate(form: FormState): FieldErrors {
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

function toInput(form: FormState): MonitorInput {
  return {
    name: form.name.trim(),
    url: form.url.trim(),
    interval_seconds: form.interval,
    timeout_seconds: Number(form.timeout),
    expected_status: Number(form.expectedStatus),
    keyword: form.keyword.trim() || null,
    group_id: form.groupId,
    customer_id: form.groupId === null ? form.customerId : null, // a group implies its customer
  };
}

/** Only the fields that differ from the saved monitor. The customer counts only when no group is set. */
function changedFields(monitor: MonitorRecord, next: MonitorInput): Partial<MonitorInput> {
  const current: Omit<MonitorInput, "customer_id"> = {
    name: monitor.name,
    url: monitor.url,
    interval_seconds: monitor.interval_seconds,
    timeout_seconds: monitor.timeout_seconds,
    expected_status: monitor.expected_status,
    keyword: monitor.keyword,
    group_id: monitor.group_id,
  };
  const keys = Object.keys(current) as (keyof typeof current)[];
  const patch: Partial<MonitorInput> = Object.fromEntries(
    keys.filter((k) => current[k] !== next[k]).map((k) => [k, next[k]]),
  );
  if (next.group_id === null && next.customer_id !== monitor.customer_id) patch.customer_id = next.customer_id;
  return patch;
}

/** Customers for the "no group" case. Optional, so a failed load just leaves the list empty. */
function useCustomers(): { customers: Customer[]; failed: boolean } {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    api
      .listCustomers()
      .then((list) => active && setCustomers(list))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, []);
  return { customers, failed };
}

function Field({ label, error, children, hint }: { label: string; error?: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="login__field monform__field">
      <span>{label}</span>
      {children}
      {error ? <em className="monform__error">{error}</em> : hint && <small className="monform__hint">{hint}</small>}
    </label>
  );
}

export function MonitorFormModal({ monitor, defaultGroupId = null, onClose, onSaved }: Props) {
  const isEdit = monitor !== undefined;
  const [form, setForm] = useState<FormState>(() => initialState(monitor, defaultGroupId));
  const [errors, setErrors] = useState<FieldErrors>({});
  const [apiError, setApiError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const { customers, failed: customersFailed } = useCustomers();
  useEscapeKey(onClose, !saving);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setErrors((prev) => ({ ...prev, [key]: undefined }));
  }

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const found = validate(form);
    setErrors(found);
    if (Object.keys(found).length > 0) return;

    const input = toInput(form);
    const patch = isEdit ? changedFields(monitor, input) : null;
    if (patch && Object.keys(patch).length === 0) return onClose();

    setSaving(true);
    setApiError(null);
    try {
      const saved = isEdit ? await api.updateMonitor(monitor.id, patch!) : await api.addMonitor(input);
      onSaved(saved);
      onClose();
    } catch (err) {
      setApiError(err instanceof ApiError ? err.message : "Could not save the website");
    } finally {
      setSaving(false);
    }
  }

  return (
    <motion.div className="modal__backdrop" onClick={onClose} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <motion.div
        className="modal monform"
        role="dialog"
        aria-modal="true"
        aria-labelledby="monform-title"
        onClick={(e) => e.stopPropagation()}
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 20, opacity: 0 }}
        transition={{ type: "spring", stiffness: 320, damping: 28 }}
      >
        <div className="modal__head">
          <h2 id="monform-title">{isEdit ? "Edit website" : "Add website"}</h2>
          <button className="banner__close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <form className="login__form monform__grid" onSubmit={submit} noValidate>
          <Field label="Name" error={errors.name}>
            <input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Shop" autoFocus maxLength={200} />
          </Field>
          <Field label="URL" error={errors.url}>
            <input
              value={form.url}
              onChange={(e) => set("url", e.target.value)}
              placeholder="https://shop.example.com"
              spellCheck={false}
              autoComplete="off"
              inputMode="url"
            />
          </Field>
          <Field label="Check interval">
            <select value={form.interval} onChange={(e) => set("interval", Number(e.target.value) as IntervalSeconds)}>
              {INTERVALS.map((i) => (
                <option key={i.value} value={i.value}>{i.label}</option>
              ))}
            </select>
          </Field>
          <div className="monform__pair">
            <Field label="Timeout (s)" error={errors.timeout}>
              <input value={form.timeout} onChange={(e) => set("timeout", e.target.value.replace(/\D/g, ""))} inputMode="numeric" />
            </Field>
            <Field label="Expected status" error={errors.expectedStatus}>
              <input value={form.expectedStatus} onChange={(e) => set("expectedStatus", e.target.value.replace(/\D/g, ""))} inputMode="numeric" />
            </Field>
          </div>
          <Field label="Keyword (optional)" error={errors.keyword} hint="The page must contain this text to count as up.">
            <input value={form.keyword} onChange={(e) => set("keyword", e.target.value)} placeholder="Welcome" maxLength={200} />
          </Field>
          <div className="login__field monform__field">
            <label htmlFor="monform-group" className="monform__label">Group</label>
            <GroupPicker id="monform-group" value={form.groupId} onChange={(g) => set("groupId", g)} disabled={saving} />
          </div>
          {form.groupId === null && (
            <div className="login__field monform__field">
              <label htmlFor="monform-customer" className="monform__label">Customer</label>
              <select
                id="monform-customer"
                className="grp-select"
                value={form.customerId ?? ""}
                disabled={saving}
                onChange={(e) => set("customerId", e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">{customersFailed ? "— No customer (could not load customers) —" : "— No customer —"}</option>
                {customers.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
          )}

          {apiError && <div className="banner banner--error" role="alert">{apiError}</div>}

          <div className="monform__actions">
            <button type="button" className="btn btn--ghost" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn--primary" disabled={saving}>
              {saving ? "Saving…" : isEdit ? "Save changes" : "Add website"}
            </button>
          </div>
        </form>
      </motion.div>
    </motion.div>
  );
}
