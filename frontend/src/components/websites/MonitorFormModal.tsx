import { useState, type FormEvent } from "react";
import { motion } from "motion/react";
import { api, ApiError } from "../../api";
import type { IntervalSeconds, MonitorRecord } from "../../types";
import { useEscapeKey } from "../../hooks/useEscapeKey";
import { useAlertSettings } from "../../hooks/useAlertSettings";
import { useCustomers } from "../../hooks/useCustomers";
import { GroupPicker } from "../groups/GroupPicker";
import { MonitorAlertFields } from "./MonitorAlertFields";
import { INTERVALS, changedFields, initialState, toInput, validate, type FieldErrors, type FormState } from "./monitorForm";

type Props = {
  /** Present = edit mode (PATCH only the changed fields); absent = create. */
  monitor?: MonitorRecord;
  /** Preselected group when creating (e.g. from a group page). */
  defaultGroupId?: number | null;
  onClose: () => void;
  onSaved: (monitor: MonitorRecord) => void;
};

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
  const alertSettings = useAlertSettings();
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

          <MonitorAlertFields
            values={form}
            settings={alertSettings}
            disabled={saving}
            onChange={(patch) => setForm((prev) => ({ ...prev, ...patch }))}
          />

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
