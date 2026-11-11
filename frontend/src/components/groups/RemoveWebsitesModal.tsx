import { useState } from "react";
import type { MonitorRecord } from "../../types";
import { ModalShell } from "./groupsUi";

type Props = {
  monitors: MonitorRecord[];
  busy: boolean;
  onClose: () => void;
  onRemove: (ids: number[]) => void;
};

/** Pick which websites leave the group. They keep being monitored, just ungrouped. */
export function RemoveWebsitesModal({ monitors, busy, onClose, onRemove }: Props) {
  const [selected, setSelected] = useState<Set<number>>(new Set());

  function toggle(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <ModalShell title="Remove websites from group" onClose={onClose} busy={busy}>
      <p className="grp-hint">The websites stay monitored — they just won’t belong to this group any more.</p>
      <ul className="grp-picklist">
        {monitors.map((m) => (
          <li key={m.id}>
            <label className="grp-check">
              <input type="checkbox" checked={selected.has(m.id)} onChange={() => toggle(m.id)} />
              <span className="grp-picklist__name">{m.name}</span>
              <span className="grp-picklist__url">{m.url}</span>
            </label>
          </li>
        ))}
      </ul>
      <div className="confirm__actions">
        <button type="button" className="btn btn--ghost" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn--primary"
          disabled={busy || selected.size === 0}
          onClick={() => onRemove([...selected])}
        >
          {busy ? "Removing…" : selected.size > 0 ? `Remove ${selected.size} from group` : "Remove from group"}
        </button>
      </div>
    </ModalShell>
  );
}
