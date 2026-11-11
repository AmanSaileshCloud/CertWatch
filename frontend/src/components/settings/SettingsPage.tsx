import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../../api";
import type { AlertSettingsAdmin } from "../../types";
import { formatRelativeTime } from "../../lib/format";
import { PageHeader } from "../layout/PageHeader";
import { DeliverySection } from "./DeliverySection";
import { RecipientsSection } from "./RecipientsSection";
import { TestEmailSection } from "./TestEmailSection";

const TITLE = "Settings";
const SUBTITLE = "Alerts & email";

function AdminsOnly() {
  return (
    <>
      <PageHeader title={TITLE} />
      <div className="empty" role="status">
        <p className="empty__title">Admins only</p>
        <p className="empty__hint">Ask an administrator to change alert and email settings.</p>
      </div>
    </>
  );
}

/** Admin-only page: alert recipients, SES email delivery and a test email. */
export function SettingsPage({ isAdmin }: { isAdmin: boolean }) {
  if (!isAdmin) return <AdminsOnly />;
  return <AlertSettingsPage />;
}

function AlertSettingsPage() {
  const [settings, setSettings] = useState<AlertSettingsAdmin | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setSettings(await api.getAdminAlertSettings());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not load the settings");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (!settings) {
    return (
      <>
        <PageHeader title={TITLE} subtitle={SUBTITLE} />
        {error ? (
          <div className="empty empty--error" role="alert">
            <p className="empty__title">Couldn't load the settings</p>
            <p className="empty__hint">{error}</p>
            <button className="btn btn--ghost" onClick={() => void load()}>Try again</button>
          </div>
        ) : (
          <div className="skeleton">
            {[0, 1, 2].map((i) => <div className="skeleton__row" key={i} style={{ animationDelay: `${i * 120}ms` }} />)}
          </div>
        )}
      </>
    );
  }

  return (
    <>
      <PageHeader title={TITLE} subtitle={SUBTITLE} />
      <div className="settings">
        <RecipientsSection settings={settings} onSaved={setSettings} />
        <DeliverySection settings={settings} onSaved={setSettings} />
        <TestEmailSection sender={settings.sender} />
        {settings.updated_by && (
          <p className="settings__footer">
            Last changed by <strong>{settings.updated_by}</strong>, {formatRelativeTime(settings.updated_at)}
          </p>
        )}
      </div>
    </>
  );
}
