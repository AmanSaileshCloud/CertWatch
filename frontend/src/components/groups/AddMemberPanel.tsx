import { useState, type FormEvent } from "react";
import { api } from "../../api";
import type { IntervalSeconds } from "../../types";
import { useToast } from "../Toast";
import { InlineError, errorText } from "./groupsUi";

const INTERVALS: Array<{ seconds: IntervalSeconds; label: string }> = [
  { seconds: 60, label: "Every 1 minute" },
  { seconds: 300, label: "Every 5 minutes" },
  { seconds: 600, label: "Every 10 minutes" },
];

type Props = { groupId: number; groupName: string; onAdded: () => void };

/** One input → adds the host as an SSL certificate and/or a website monitor in this group. */
export function AddMemberPanel({ groupId, groupName, onAdded }: Props) {
  const toast = useToast();
  const [target, setTarget] = useState("");
  const [addSsl, setAddSsl] = useState(true);
  const [addWebsite, setAddWebsite] = useState(true);
  const [interval, setIntervalSeconds] = useState<IntervalSeconds>(300);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const nothingChosen = !addSsl && !addWebsite;
  const trimmed = target.trim();

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!trimmed || nothingChosen) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.addGroupMember(groupId, {
        target: trimmed,
        add_ssl: addSsl,
        add_website: addWebsite,
        interval_seconds: interval,
      });
      const messages = result.messages.length > 0 ? result.messages : [`Added ${trimmed} to ${groupName}`];
      messages.forEach((m) => toast.show(m, "success"));
      setTarget("");
      onAdded();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="grp-panel grp-add" onSubmit={submit} aria-labelledby="add-member-title" noValidate>
      <h2 id="add-member-title" className="grp-section__title">Add to this group</h2>
      <div className="grp-add__row">
        <label className="login__field grp-add__target">
          <span>Domain or URL</span>
          <input
            value={target}
            onChange={(e) => {
              setTarget(e.target.value);
              setError(null);
            }}
            placeholder="shop.acme.com"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? "add-member-error" : undefined}
          />
        </label>
        {addWebsite && (
          <label className="login__field grp-add__interval">
            <span>Check website</span>
            <select
              className="grp-select"
              value={interval}
              onChange={(e) => setIntervalSeconds(Number(e.target.value) as IntervalSeconds)}
            >
              {INTERVALS.map((i) => (
                <option key={i.seconds} value={i.seconds}>
                  {i.label}
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="submit" className="btn btn--primary grp-add__submit" disabled={busy || !trimmed || nothingChosen}>
          <span className="btn__plus" aria-hidden="true">+</span>
          {busy ? "Adding…" : "Add"}
        </button>
      </div>
      <fieldset className="grp-add__checks">
        <legend className="grp-sr-only">What to monitor</legend>
        <label className="grp-check">
          <input type="checkbox" checked={addSsl} onChange={(e) => setAddSsl(e.target.checked)} />
          SSL certificate
        </label>
        <label className="grp-check">
          <input type="checkbox" checked={addWebsite} onChange={(e) => setAddWebsite(e.target.checked)} />
          Website uptime
        </label>
        {nothingChosen && (
          <span className="grp-hint grp-hint--warn" role="status">
            Tick at least one.
          </span>
        )}
      </fieldset>
      <div id="add-member-error">
        <InlineError message={error} />
      </div>
    </form>
  );
}
