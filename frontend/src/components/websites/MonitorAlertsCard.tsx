import { useEffect, useState } from "react";
import { api, ApiError } from "../../api";
import type { AlertRule, MonitorRecord } from "../../types";
import { useAlertSettings } from "../../hooks/useAlertSettings";
import { timingSummary } from "../../lib/alerts";
import { useToast } from "../Toast";
import { AlertRecipientsEditor, type InheritedRecipient } from "../alerts/AlertRecipientsEditor";

/** Alert rules for one customer, or the global ones (no customer_id); empty if skipped (null) or they can't be loaded. */
function useRules(scope: { customer_id?: number } | null): AlertRule[] {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const skip = scope === null;
  const customerId = scope?.customer_id;
  useEffect(() => {
    if (skip) {
      setRules([]);
      return;
    }
    let active = true;
    api
      .listAlertRules(customerId === undefined ? {} : { customer_id: customerId })
      .then((list) => active && setRules(list))
      .catch(() => active && setRules([]));
    return () => {
      active = false;
    };
  }, [skip, customerId]);
  return rules;
}

function enabledAs(rules: AlertRule[], source: string): InheritedRecipient[] {
  return rules.filter((r) => r.enabled).map((r) => ({ target: r.target, source }));
}

/** Alert status, timing, recipients and a test button for one website. */
export function MonitorAlertsCard({ monitor }: { monitor: MonitorRecord }) {
  const toast = useToast();
  const settings = useAlertSettings();
  const globalRules = useRules({});
  const customerRules = useRules(monitor.customer_id === null ? null : { customer_id: monitor.customer_id });
  const [testing, setTesting] = useState(false);

  const inherited: InheritedRecipient[] = [
    ...(settings?.default_recipients ?? []).map((target) => ({ target, source: "default" })),
    ...enabledAs(globalRules, "global"),
    ...enabledAs(customerRules, "customer"),
  ];

  async function sendTest() {
    setTesting(true);
    try {
      const res = await api.testMonitorAlert(monitor.id);
      toast.show(res.message, "success");
    } catch (e) {
      if (e instanceof ApiError) toast.show(e.message, e.status === 429 ? "info" : "error");
      else toast.show("Could not send the test alert", "error");
    } finally {
      setTesting(false);
    }
  }

  const hasTiming = monitor.effective_alert_delay_minutes !== undefined && monitor.effective_reminder_minutes !== undefined;
  return (
    <section className="panel">
      <div className="alertcard__head">
        <h3 className="panel__title alertcard__title">Alerts</h3>
        <button type="button" className="btn btn--ghost btn--icon" onClick={() => void sendTest()} disabled={testing}>
          {testing ? "Sending…" : "Send test alert"}
        </button>
      </div>
      <p className="alertcard__status">
        <span className={`result ${monitor.alerts_enabled ? "result--up" : "result--down"}`}>
          {monitor.alerts_enabled ? "On" : "Off"}
        </span>
        {hasTiming && (
          <span className="mono-dim">
            {timingSummary(monitor.effective_alert_delay_minutes, monitor.effective_reminder_minutes)}
          </span>
        )}
      </p>
      {settings?.notifier === "console" && (
        <p className="drawer__hint alertcard__note">Local mode: alerts are written to the worker log, not emailed.</p>
      )}
      <h4 className="monform__label alertcard__sub">Recipients</h4>
      <AlertRecipientsEditor scope={{ monitor_id: monitor.id }} inherited={inherited} />
    </section>
  );
}
