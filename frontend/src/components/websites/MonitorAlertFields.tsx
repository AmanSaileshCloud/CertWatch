import type { AlertDelayMinutes, AlertSettings, ReminderMinutes } from "../../types";
import { DELAY_CHOICES, REMINDER_CHOICES, delayLabel, reminderLabel } from "../../lib/alerts";

export interface AlertFieldValues {
  alertsEnabled: boolean;
  /** null = use the global default. */
  alertDelayMinutes: AlertDelayMinutes | null;
  reminderMinutes: ReminderMinutes | null;
}

type Props = {
  values: AlertFieldValues;
  settings: AlertSettings | null;
  disabled?: boolean;
  onChange: (patch: Partial<AlertFieldValues>) => void;
};

/** "" in a <select> stands for null (use the default). */
function parseChoice<T extends number>(raw: string): T | null {
  return raw === "" ? null : (Number(raw) as T);
}

function defaultOption(label: string | null): string {
  return label ? `Use default (${label})` : "Use default";
}

/** The "Alerts" part of the add/edit website form. */
export function MonitorAlertFields({ values, settings, disabled, onChange }: Props) {
  return (
    <fieldset className="alertfields" disabled={disabled}>
      <legend className="monform__label">Alerts</legend>
      <label className="toggle alertfields__toggle">
        <input
          type="checkbox"
          checked={values.alertsEnabled}
          onChange={(e) => onChange({ alertsEnabled: e.target.checked })}
        />
        <span className="toggle__track" aria-hidden="true">
          <span className="toggle__thumb" />
        </span>
        <span className="toggle__label">Email alerts {values.alertsEnabled ? "on" : "off"}</span>
      </label>

      <div className="monform__pair">
        <label className="login__field monform__field">
          <span>Alert after down for</span>
          <select
            value={values.alertDelayMinutes ?? ""}
            disabled={!values.alertsEnabled}
            onChange={(e) => onChange({ alertDelayMinutes: parseChoice<AlertDelayMinutes>(e.target.value) })}
          >
            <option value="">{defaultOption(settings && delayLabel(settings.alert_delay_minutes))}</option>
            {DELAY_CHOICES.map((m) => (
              <option key={m} value={m}>{delayLabel(m)}</option>
            ))}
          </select>
        </label>
        <label className="login__field monform__field">
          <span>Remind every</span>
          <select
            value={values.reminderMinutes ?? ""}
            disabled={!values.alertsEnabled}
            onChange={(e) => onChange({ reminderMinutes: parseChoice<ReminderMinutes>(e.target.value) })}
          >
            <option value="">{defaultOption(settings && reminderLabel(settings.reminder_minutes))}</option>
            {REMINDER_CHOICES.map((m) => (
              <option key={m} value={m}>{reminderLabel(m)}</option>
            ))}
          </select>
        </label>
      </div>
      <small className="monform__hint">Alerts are sent by email (SES).</small>
    </fieldset>
  );
}
