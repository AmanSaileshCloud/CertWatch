import { useEffect, useState, type KeyboardEvent } from "react";
import { motion } from "motion/react";
import { api, ApiError } from "../api";
import type { CertInfo, DomainRecord } from "../types";
import { StatusBadge } from "./StatusBadge";
import { useToast } from "./Toast";

type Props = {
  record: DomainRecord;
  onClose: () => void;
  onSaved: (updated: DomainRecord) => void;
};

function fmt(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function DomainDetail({ record, onClose, onSaved }: Props) {
  const toast = useToast();
  const [emails, setEmails] = useState<string[]>(record.notify_emails);
  const [alertsEnabled, setAlertsEnabled] = useState(record.alerts_enabled);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [testingAlert, setTestingAlert] = useState(false);

  const [cert, setCert] = useState<CertInfo | null>(null);
  const [certState, setCertState] = useState<"loading" | "error" | "done">("loading");
  const [certErr, setCertErr] = useState("");

  useEffect(() => {
    let cancelled = false;
    setCertState("loading");
    api
      .getCertDetails(record.domain)
      .then((c) => { if (!cancelled) { setCert(c); setCertState("done"); } })
      .catch((e) => {
        if (!cancelled) {
          setCertErr(e instanceof ApiError ? e.message : "Could not read certificate");
          setCertState("error");
        }
      });
    return () => { cancelled = true; };
  }, [record.domain]);

  const dirty =
    alertsEnabled !== record.alerts_enabled ||
    emails.join(",") !== record.notify_emails.join(",");

  function addEmail() {
    const e = draft.trim().toLowerCase();
    if (e && !emails.includes(e)) setEmails([...emails, e]);
    setDraft("");
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addEmail();
    }
  }

  async function sendTestAlert() {
    setTestingAlert(true);
    try {
      await api.testAlert(record.domain);
      toast.show("Test alert sent — check your notifier output", "success");
    } catch (err) {
      toast.show(err instanceof ApiError ? err.message : "Failed to send test alert", "error");
    } finally {
      setTestingAlert(false);
    }
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.updateDomain(record.domain, {
        notify_emails: emails,
        alerts_enabled: alertsEnabled,
      });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 1800);
      onSaved(updated);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <motion.div
      className="drawer__backdrop"
      onClick={onClose}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <motion.aside
        className="drawer"
        onClick={(e) => e.stopPropagation()}
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", stiffness: 320, damping: 34 }}
      >
        <div className="drawer__head">
          <div>
            <h2 className="drawer__title">{record.host}</h2>
            <span className="drawer__sub">
              {record.domain.endsWith(":443") ? "port 443" : `port ${record.port}`}
            </span>
          </div>
          <button className="banner__close" onClick={onClose} aria-label="close">
            ✕
          </button>
        </div>

        <div className="drawer__statusrow">
          <StatusBadge status={record.status} />
          <span className="drawer__days">
            {record.days_remaining === null ? "—" : `${record.days_remaining} days left`}
          </span>
        </div>

        <section className="drawer__section">
          <h3>Certificate</h3>
          <dl className="detail">
            <dt>Expires</dt>
            <dd>{fmt(record.not_after)}</dd>
            <dt>Status</dt>
            <dd>{record.status}</dd>
            <dt>Last checked</dt>
            <dd>{fmt(record.last_checked_at)}</dd>
            {record.last_error && (
              <>
                <dt>Last error</dt>
                <dd className="detail__err">{record.last_error}</dd>
              </>
            )}
            <dt>Last alert sent</dt>
            <dd>
              {record.last_alert_threshold === null
                ? "none"
                : `${record.last_alert_threshold}-day threshold`}
            </dd>
            <dt>Added</dt>
            <dd>{fmt(record.created_at)}</dd>
          </dl>
        </section>

        <section className="drawer__section">
          <h3>Certificate details</h3>
          {certState === "loading" && <p className="drawer__hint">Reading certificate…</p>}
          {certState === "error" && <div className="banner banner--error">{certErr}</div>}
          {certState === "done" && cert && (
            <dl className="detail">
              <dt>Issued to</dt>
              <dd>{cert.subject}</dd>
              <dt>Issued by</dt>
              <dd>{cert.issuer}</dd>
              <dt>Valid from</dt>
              <dd>{fmt(cert.not_before)}</dd>
              <dt>Valid until</dt>
              <dd>{fmt(cert.not_after)}</dd>
              <dt>Key</dt>
              <dd>{cert.key_type}{cert.key_bits ? ` · ${cert.key_bits}-bit` : ""}</dd>
              <dt>Signature</dt>
              <dd>{cert.sig_algorithm}</dd>
              <dt>Serial</dt>
              <dd className="detail__mono">{cert.serial}</dd>
              <dt>Covers ({cert.sans.length})</dt>
              <dd>
                <div className="sans">
                  {cert.sans.length
                    ? cert.sans.map((s) => <span key={s} className="sans__item">{s}</span>)
                    : "—"}
                </div>
              </dd>
            </dl>
          )}
        </section>

        <section className="drawer__section">
          <h3>Notifications</h3>

          <label className="toggle">
            <input
              type="checkbox"
              checked={alertsEnabled}
              onChange={(e) => setAlertsEnabled(e.target.checked)}
            />
            <span className="toggle__track" aria-hidden="true">
              <span className="toggle__thumb" />
            </span>
            <span className="toggle__label">
              Alerts {alertsEnabled ? "enabled" : "muted"}
            </span>
          </label>

          <div className="drawer__field">
            <span className="drawer__field-label">Recipient emails</span>
            <div className="chips">
              {emails.map((e) => (
                <span key={e} className="chip-tag">
                  {e}
                  <button onClick={() => setEmails(emails.filter((x) => x !== e))} aria-label={`remove ${e}`}>
                    ✕
                  </button>
                </span>
              ))}
              <input
                className="chips__input"
                value={draft}
                onChange={(ev) => setDraft(ev.target.value)}
                onKeyDown={onKey}
                onBlur={addEmail}
                placeholder={emails.length ? "add another…" : "ops@company.com"}
              />
            </div>
            <p className="drawer__hint">
              Press Enter to add. Leave empty to use the global default recipient.
            </p>
          </div>

          {error && <div className="banner banner--error">{error}</div>}

          <div className="drawer__actions">
            <motion.button
              className="btn btn--primary drawer__save"
              onClick={save}
              disabled={saving || !dirty}
              whileTap={{ scale: 0.97 }}
            >
              {saving ? "Saving…" : saved ? "Saved ✓" : "Save changes"}
            </motion.button>

            <motion.button
              className="btn btn--ghost drawer__test-alert"
              onClick={sendTestAlert}
              disabled={testingAlert}
              whileTap={{ scale: 0.97 }}
              title="Send a test notification to verify your alert setup"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>
              </svg>
              {testingAlert ? "Sending…" : "Send test alert"}
            </motion.button>
          </div>
        </section>
      </motion.aside>
    </motion.div>
  );
}
