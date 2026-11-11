import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../../api";
import type { AlertRule } from "../../types";
import { isValidEmail } from "../../lib/alerts";

export type AlertScope = { customer_id: number } | { monitor_id: number };

/** A recipient that comes from elsewhere (global defaults, the customer) and can't be edited here. */
export interface InheritedRecipient {
  target: string;
  source: string;
}

type Props = {
  scope: AlertScope;
  inherited?: InheritedRecipient[];
  /** Show per-rule "Down" / "Recovery" checkboxes. */
  showKinds?: boolean;
};

type RulePatch = { on_down?: boolean; on_recovery?: boolean };

function messageOf(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}

/** Email recipients for one customer or one monitor: list, add, remove (and optionally pick alert kinds). */
export function AlertRecipientsEditor({ scope, inherited = [], showKinds = false }: Props) {
  const customerId = "customer_id" in scope ? scope.customer_id : undefined;
  const monitorId = "monitor_id" in scope ? scope.monitor_id : undefined;
  const [rules, setRules] = useState<AlertRule[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    api
      .listAlertRules({ customer_id: customerId, monitor_id: monitorId })
      .then((list) => active && setRules(list))
      .catch((e) => active && setLoadError(messageOf(e, "Could not load recipients")));
    return () => {
      active = false;
    };
  }, [customerId, monitorId]);

  async function addRule(target: string) {
    const created = await api.addAlertRule({ target, customer_id: customerId, monitor_id: monitorId });
    setRules((prev) => [...(prev ?? []), created]);
  }

  async function runOnRule(rule: AlertRule, action: () => Promise<void>, fallback: string) {
    setPendingId(rule.id);
    setActionError(null);
    try {
      await action();
    } catch (e) {
      setActionError(messageOf(e, fallback));
    } finally {
      setPendingId(null);
    }
  }

  const removeRule = (rule: AlertRule) =>
    runOnRule(rule, async () => {
      await api.deleteAlertRule(rule.id);
      setRules((prev) => (prev ?? []).filter((r) => r.id !== rule.id));
    }, "Could not remove the recipient");

  const updateRule = (rule: AlertRule, patch: RulePatch) =>
    runOnRule(rule, async () => {
      const saved = await api.updateAlertRule(rule.id, patch);
      setRules((prev) => (prev ?? []).map((r) => (r.id === saved.id ? saved : r)));
    }, "Could not update the recipient");

  if (loadError) return <p className="grp-hint grp-hint--warn">Couldn't load recipients: {loadError}</p>;

  const isEmpty = inherited.length === 0 && rules?.length === 0;
  return (
    <div className="alertrcpt">
      {rules === null ? (
        <p className="alertrcpt__muted">Loading recipients…</p>
      ) : isEmpty ? (
        <p className="alertrcpt__muted">No recipients yet.</p>
      ) : (
        <ul className="alertrcpt__list">
          {inherited.map((r) => (
            <li key={`${r.source}:${r.target}`} className="alertrcpt__row">
              <span className="alertrcpt__email">{r.target}</span>
              <span className="alertrcpt__tag">{r.source}</span>
            </li>
          ))}
          {rules.map((rule) => (
            <RuleRow
              key={rule.id}
              rule={rule}
              showKinds={showKinds}
              busy={pendingId === rule.id}
              onUpdate={(patch) => void updateRule(rule, patch)}
              onRemove={() => void removeRule(rule)}
            />
          ))}
        </ul>
      )}
      {actionError && <p className="monform__error" role="alert">{actionError}</p>}
      <AddRecipientForm onAdd={addRule} />
    </div>
  );
}

function RuleRow({ rule, showKinds, busy, onUpdate, onRemove }: {
  rule: AlertRule;
  showKinds: boolean;
  busy: boolean;
  onUpdate: (patch: RulePatch) => void;
  onRemove: () => void;
}) {
  return (
    <li className="alertrcpt__row">
      <span className="alertrcpt__email">{rule.target}</span>
      {showKinds && (
        <span className="alertrcpt__kinds">
          <label className="grp-check">
            <input type="checkbox" checked={rule.on_down} disabled={busy} onChange={(e) => onUpdate({ on_down: e.target.checked })} />
            Down
          </label>
          <label className="grp-check">
            <input type="checkbox" checked={rule.on_recovery} disabled={busy} onChange={(e) => onUpdate({ on_recovery: e.target.checked })} />
            Recovery
          </label>
        </span>
      )}
      <button type="button" className="alertrcpt__remove" onClick={onRemove} disabled={busy} aria-label={`Remove ${rule.target}`}>
        ×
      </button>
    </li>
  );
}

function AddRecipientForm({ onAdd }: { onAdd: (target: string) => Promise<void> }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const target = value.trim();
    if (!isValidEmail(target)) return setError("Enter an email address like name@example.com.");
    setSaving(true);
    setError(null);
    try {
      await onAdd(target);
      setValue("");
    } catch (err) {
      setError(messageOf(err, "Could not add the recipient"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="alertrcpt__add" onSubmit={submit} noValidate>
      <input
        type="email"
        className="alertrcpt__input"
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setError(null);
        }}
        placeholder="name@example.com"
        aria-label="Recipient email"
        aria-invalid={error !== null}
        maxLength={254}
        disabled={saving}
      />
      <button type="submit" className="btn btn--ghost btn--icon" disabled={saving || !value.trim()}>
        {saving ? "Adding…" : "Add"}
      </button>
      {error && <em className="monform__error alertrcpt__error" role="alert">{error}</em>}
    </form>
  );
}
