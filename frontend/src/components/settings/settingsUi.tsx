import { useState, type ReactNode } from "react";
import { api, ApiError } from "../../api";
import type { AlertSettingsAdmin, AlertSettingsUpdate } from "../../types";
import { useToast } from "../Toast";

/** Saves a partial update; keeps the API error (e.g. a 422 detail) for display next to the Save button. */
export function useSettingsSave(onSaved: (next: AlertSettingsAdmin) => void) {
  const toast = useToast();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(patch: AlertSettingsUpdate, successMessage = "Settings saved"): Promise<AlertSettingsAdmin | null> {
    setSaving(true);
    setError(null);
    try {
      const next = await api.updateAdminAlertSettings(patch);
      onSaved(next);
      toast.show(successMessage, "success");
      return next;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save the settings");
      return null;
    } finally {
      setSaving(false);
    }
  }

  return { saving, error, setError, save };
}

type FieldProps = {
  label: string;
  htmlFor?: string;
  /** undefined = this field has no server default (no badge, no reset link). */
  fromServer?: boolean;
  onReset?: () => void;
  error?: string | null;
  hint?: ReactNode;
  children: ReactNode;
};

/** A labelled settings field with a "from server config" badge or a "Reset to server default" link. */
export function SettingField({ label, htmlFor, fromServer, onReset, error, hint, children }: FieldProps) {
  return (
    <div className="login__field monform__field settings__field">
      <div className="settings__fieldhead">
        <label htmlFor={htmlFor} className="monform__label">{label}</label>
        {fromServer === true && <small className="settings__badge">from server config</small>}
        {fromServer === false && onReset && (
          <button type="button" className="settings__reset" onClick={onReset}>Reset to server default</button>
        )}
      </div>
      {children}
      {error ? <em className="monform__error" role="alert">{error}</em> : hint && <small className="monform__hint">{hint}</small>}
    </div>
  );
}

/** Inline API error + the section's Save button. */
export function SaveBar({ dirty, saving, error, label = "Save" }: {
  dirty: boolean;
  saving: boolean;
  error: string | null;
  label?: string;
}) {
  return (
    <div className="settings__savebar">
      {error && <p className="monform__error settings__saveerror" role="alert">{error}</p>}
      <button type="submit" className="btn btn--primary" disabled={!dirty || saving}>
        {saving ? "Saving…" : label}
      </button>
    </div>
  );
}
