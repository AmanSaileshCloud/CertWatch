import { useState, type FormEvent } from "react";
import type { AlertDelayMinutes, AlertSettingsAdmin, AlertSettingsUpdate, ReminderMinutes } from "../../types";
import { DELAY_CHOICES, REMINDER_CHOICES, delayLabel, isValidEmail, reminderLabel } from "../../lib/alerts";
import { RecipientChips } from "./RecipientChips";
import { SaveBar, SettingField, useSettingsSave } from "./settingsUi";

type Key = "sender" | "default_recipients" | "alert_delay_minutes" | "reminder_minutes";
type Values = Pick<AlertSettingsAdmin, Key>;

interface Draft {
  values: Values;
  /** true = use the server (env) default; saving sends null if a UI value is currently stored. */
  useServer: Record<Key, boolean>;
}

const KEYS: Key[] = ["sender", "default_recipients", "alert_delay_minutes", "reminder_minutes"];

function fromSettings(s: AlertSettingsAdmin): Draft {
  return {
    values: {
      sender: s.sender,
      default_recipients: s.default_recipients,
      alert_delay_minutes: s.alert_delay_minutes,
      reminder_minutes: s.reminder_minutes,
    },
    useServer: {
      sender: !s.saved.sender,
      default_recipients: !s.saved.default_recipients,
      alert_delay_minutes: !s.saved.alert_delay_minutes,
      reminder_minutes: !s.saved.reminder_minutes,
    },
  };
}

/** Only the fields whose value or source (UI vs server config) changed. An empty sender means "use the server default". */
function buildPatch(s: AlertSettingsAdmin, draft: Draft): AlertSettingsUpdate {
  const patch: Record<string, unknown> = {};
  for (const key of KEYS) {
    const value = key === "sender" ? draft.values.sender?.trim() || null : draft.values[key];
    const useServer = draft.useServer[key] || value === null;
    if (useServer) {
      if (s.saved[key]) patch[key] = null;
    } else if (!s.saved[key] || JSON.stringify(value) !== JSON.stringify(s[key])) {
      patch[key] = value;
    }
  }
  return patch as AlertSettingsUpdate;
}

type Props = {
  settings: AlertSettingsAdmin;
  onSaved: (next: AlertSettingsAdmin) => void;
};

/** Sender, default recipients and default alert timing. */
export function RecipientsSection({ settings, onSaved }: Props) {
  const [draft, setDraft] = useState<Draft>(() => fromSettings(settings));
  const [senderError, setSenderError] = useState<string | null>(null);
  const { saving, error, save } = useSettingsSave(onSaved);
  const patch = buildPatch(settings, draft);
  const dirty = Object.keys(patch).length > 0;

  function setValue<K extends Key>(key: K, value: Values[K]) {
    setDraft((d) => ({ values: { ...d.values, [key]: value }, useServer: { ...d.useServer, [key]: false } }));
  }

  function reset(key: Key) {
    setDraft((d) => ({
      values: { ...d.values, [key]: settings.env_defaults[key] },
      useServer: { ...d.useServer, [key]: true },
    }));
    if (key === "sender") setSenderError(null);
  }

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const sender = draft.values.sender?.trim();
    if (sender && !isValidEmail(sender)) return setSenderError("Enter an email address like alerts@example.com.");
    const next = await save(patch, "Alert recipients saved");
    if (next) setDraft(fromSettings(next));
  }

  const fromServer = (key: Key) => draft.useServer[key];
  return (
    <form className="panel settings__section" onSubmit={submit} noValidate>
      <h3 className="panel__title">Alert recipients</h3>
      <fieldset className="settings__fields" disabled={saving}>
        <SettingField
          label="Sender (From) email"
          htmlFor="settings-sender"
          fromServer={fromServer("sender")}
          onReset={() => reset("sender")}
          error={senderError}
          hint="Must be a verified identity in Amazon SES."
        >
          <input
            id="settings-sender"
            type="email"
            value={draft.values.sender ?? ""}
            onChange={(e) => {
              setValue("sender", e.target.value);
              setSenderError(null);
            }}
            placeholder="alerts@example.com"
            maxLength={254}
            spellCheck={false}
            aria-invalid={senderError !== null}
          />
        </SettingField>

        <SettingField
          label="Default recipients"
          htmlFor="settings-recipients"
          fromServer={fromServer("default_recipients")}
          onReset={() => reset("default_recipients")}
          hint="Press Enter to add. Every website alert goes to these addresses, plus any customer or website recipients."
        >
          <RecipientChips
            id="settings-recipients"
            value={draft.values.default_recipients}
            disabled={saving}
            onChange={(next) => setValue("default_recipients", next)}
          />
        </SettingField>

        <div className="monform__pair">
          <SettingField
            label="Default alert delay"
            htmlFor="settings-delay"
            fromServer={fromServer("alert_delay_minutes")}
            onReset={() => reset("alert_delay_minutes")}
          >
            <select
              id="settings-delay"
              value={draft.values.alert_delay_minutes}
              onChange={(e) => setValue("alert_delay_minutes", Number(e.target.value) as AlertDelayMinutes)}
            >
              {DELAY_CHOICES.map((m) => (
                <option key={m} value={m}>{delayLabel(m)}</option>
              ))}
            </select>
          </SettingField>
          <SettingField
            label="Default reminder"
            htmlFor="settings-reminder"
            fromServer={fromServer("reminder_minutes")}
            onReset={() => reset("reminder_minutes")}
          >
            <select
              id="settings-reminder"
              value={draft.values.reminder_minutes}
              onChange={(e) => setValue("reminder_minutes", Number(e.target.value) as ReminderMinutes)}
            >
              {REMINDER_CHOICES.map((m) => (
                <option key={m} value={m}>{reminderLabel(m)}</option>
              ))}
            </select>
          </SettingField>
        </div>
      </fieldset>
      <SaveBar dirty={dirty} saving={saving} error={error} />
    </form>
  );
}
