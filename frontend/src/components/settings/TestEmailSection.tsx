import { useState, type FormEvent } from "react";
import { api, ApiError } from "../../api";
import { isValidEmail } from "../../lib/alerts";
import { useToast } from "../Toast";
import { SettingField } from "./settingsUi";

/** Sends one email through the saved delivery settings so an admin can confirm SES works. */
export function TestEmailSection({ sender }: { sender: string | null }) {
  const toast = useToast();
  const [to, setTo] = useState(sender ?? "");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const address = to.trim();
    if (!isValidEmail(address)) return setError("Enter an email address like name@example.com.");
    setSending(true);
    setError(null);
    try {
      const res = await api.sendSettingsTestEmail(address);
      toast.show(res.message, "success");
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : "Could not send the test email";
      toast.show(`Test email failed: ${detail}`, "error");
    } finally {
      setSending(false);
    }
  }

  return (
    <form className="panel settings__section" onSubmit={submit} noValidate>
      <h3 className="panel__title">Send test email</h3>
      <p className="grp-hint">Uses the saved settings above — save your changes first.</p>
      <div className="settings__testrow">
        <SettingField label="Send to" htmlFor="settings-test-to" error={error}>
          <input
            id="settings-test-to"
            type="email"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              setError(null);
            }}
            placeholder="me@example.com"
            maxLength={254}
            spellCheck={false}
            disabled={sending}
            aria-invalid={error !== null}
          />
        </SettingField>
        <button type="submit" className="btn btn--primary" disabled={sending || !to.trim()}>
          {sending ? "Sending…" : "Send test email"}
        </button>
      </div>
    </form>
  );
}
