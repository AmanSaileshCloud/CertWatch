import { useState, type FormEvent } from "react";
import { AnimatePresence } from "motion/react";
import type { AlertSettingsAdmin, AlertSettingsUpdate, EmailTransport } from "../../types";
import { ConfirmDialog } from "../ConfirmDialog";
import { SaveBar, SettingField, useSettingsSave } from "./settingsUi";
import { SmtpPasswordField } from "./SmtpPasswordField";

const SMTP_HOST_PATTERN = /^email-smtp\.[a-z0-9-]+\.amazonaws\.com$/;
const SMTP_PORTS = [587, 25, 465, 2465, 2587];

const TRANSPORTS: { value: EmailTransport; label: string; hint: string }[] = [
  { value: "iam_role", label: "AWS IAM role", hint: "Recommended on EC2 — no password needed." },
  { value: "smtp", label: "SES SMTP credentials", hint: "Username and password generated in the SES console." },
];

interface Draft {
  transport: EmailTransport;
  host: string;
  port: number;
  username: string;
  password: string;
  replacingPassword: boolean;
}

type Errors = Partial<Record<"host" | "username" | "password", string>>;

function fromSettings(s: AlertSettingsAdmin): Draft {
  return {
    transport: s.transport,
    host: s.smtp_host ?? `email-smtp.${s.aws_region}.amazonaws.com`,
    port: s.smtp_port || 587,
    username: s.smtp_username ?? "",
    password: "",
    replacingPassword: false,
  };
}

/** SMTP fields are only sent while SMTP is selected, so the prefilled endpoint never leaks into an IAM-role save. */
function buildPatch(s: AlertSettingsAdmin, d: Draft): AlertSettingsUpdate {
  const patch: AlertSettingsUpdate = {};
  if (d.transport !== s.transport) patch.transport = d.transport;
  if (d.transport !== "smtp") return patch;
  const host = d.host.trim() || null;
  const username = d.username.trim() || null;
  if (host !== s.smtp_host) patch.smtp_host = host;
  if (d.port !== s.smtp_port) patch.smtp_port = d.port;
  if (username !== s.smtp_username) patch.smtp_username = username;
  if (d.password) patch.smtp_password = d.password;
  return patch;
}

function validate(s: AlertSettingsAdmin, d: Draft): Errors {
  if (d.transport !== "smtp") return {};
  const errors: Errors = {};
  if (!SMTP_HOST_PATTERN.test(d.host.trim())) errors.host = "Use the SES SMTP endpoint, e.g. email-smtp.ap-south-1.amazonaws.com.";
  if (!d.username.trim()) errors.username = "The SMTP username is required.";
  if (!d.password && !s.smtp_password_set) errors.password = "The SMTP password is required.";
  return errors;
}

type Props = {
  settings: AlertSettingsAdmin;
  onSaved: (next: AlertSettingsAdmin) => void;
};

/** How alert emails reach Amazon SES: the instance's IAM role, or SES SMTP credentials. */
export function DeliverySection({ settings, onSaved }: Props) {
  const [draft, setDraft] = useState<Draft>(() => fromSettings(settings));
  const [errors, setErrors] = useState<Errors>({});
  const [confirmRemove, setConfirmRemove] = useState(false);
  const { saving, error, save } = useSettingsSave(onSaved);
  const patch = buildPatch(settings, draft);

  function set<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
    setErrors((e) => ({ ...e, [key]: undefined }));
  }

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const found = validate(settings, draft);
    setErrors(found);
    if (Object.values(found).some(Boolean)) return;
    const next = await save(patch, "Email delivery saved");
    if (next) setDraft(fromSettings(next));
  }

  // SMTP delivery can't run without a password, so while SMTP is the saved transport,
  // removing it also switches delivery to the IAM role (one save, accepted by the API).
  const removeSwitchesTransport = settings.transport === "smtp";

  async function removePassword() {
    setConfirmRemove(false);
    const patch: AlertSettingsUpdate = removeSwitchesTransport
      ? { transport: "iam_role", smtp_password: null }
      : { smtp_password: null };
    const toast = removeSwitchesTransport
      ? "SMTP password removed; delivery switched to the IAM role"
      : "SMTP password removed";
    const next = await save(patch, toast);
    if (next) setDraft(fromSettings(next));
  }

  return (
    <>
      <form className="panel settings__section" onSubmit={submit} noValidate>
        <h3 className="panel__title">Email delivery (Amazon SES)</h3>
        <fieldset className="settings__fields" disabled={saving}>
          <div className="settings__radios" role="radiogroup" aria-label="How to send email">
            {TRANSPORTS.map((t) => (
              <label key={t.value} className={`settings__radio ${draft.transport === t.value ? "is-checked" : ""}`}>
                <input
                  type="radio"
                  name="settings-transport"
                  value={t.value}
                  checked={draft.transport === t.value}
                  onChange={() => set("transport", t.value)}
                />
                <strong>{t.label}</strong>
                <small>{t.hint}</small>
              </label>
            ))}
          </div>

          {draft.transport === "smtp" && (
            <>
              <div className="settings__hostport">
                <SettingField label="SMTP endpoint" htmlFor="settings-smtp-host" error={errors.host}>
                  <input
                    id="settings-smtp-host"
                    value={draft.host}
                    onChange={(e) => set("host", e.target.value)}
                    placeholder={`email-smtp.${settings.aws_region}.amazonaws.com`}
                    spellCheck={false}
                    autoComplete="off"
                    aria-invalid={Boolean(errors.host)}
                  />
                </SettingField>
                <SettingField label="Port" htmlFor="settings-smtp-port">
                  <select id="settings-smtp-port" value={draft.port} onChange={(e) => set("port", Number(e.target.value))}>
                    {SMTP_PORTS.map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                </SettingField>
              </div>
              <SettingField label="SMTP username" htmlFor="settings-smtp-user" error={errors.username}>
                <input
                  id="settings-smtp-user"
                  value={draft.username}
                  onChange={(e) => set("username", e.target.value)}
                  placeholder="AKIA…"
                  spellCheck={false}
                  autoComplete="off"
                  aria-invalid={Boolean(errors.username)}
                />
              </SettingField>
              <SmtpPasswordField
                value={draft.password}
                isSet={settings.smtp_password_set}
                replacing={draft.replacingPassword}
                encryptionAvailable={settings.encryption_available}
                error={errors.password}
                onChange={(v) => set("password", v)}
                onReplace={(replacing) => set("replacingPassword", replacing)}
                onRemove={() => setConfirmRemove(true)}
              />
            </>
          )}
        </fieldset>
        <SaveBar dirty={Object.keys(patch).length > 0} saving={saving} error={error} />
      </form>

      {/* Outside the form: the dialog's buttons would otherwise submit it. */}
      <AnimatePresence>
        {confirmRemove && (
          <ConfirmDialog
            title="Remove the SMTP password?"
            message={
              removeSwitchesTransport
                ? "Email delivery will switch to the AWS IAM role. Make sure this server's IAM role can send through SES, or alert emails will fail."
                : "The saved SMTP password will be deleted. SMTP delivery will need a new password before it can be used."
            }
            onConfirm={() => void removePassword()}
            onCancel={() => setConfirmRemove(false)}
          />
        )}
      </AnimatePresence>
    </>
  );
}
