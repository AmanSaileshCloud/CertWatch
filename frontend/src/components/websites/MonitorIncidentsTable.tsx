import type { IncidentRecord } from "../../types";
import { formatDateTime, formatDuration, plural } from "../../lib/format";

const CLOSE_REASONS: Record<string, string> = {
  recovered: "Recovered",
  paused: "Paused",
  edited: "Settings edited",
};

/** "DOWN ✓ · 2 reminders · RECOVERED ✓", with the exact send times for the tooltip. */
function alertSummary(i: IncidentRecord): { text: string; title: string } {
  const reminders = i.reminder_count ?? 0;
  const parts: string[] = [];
  const times: string[] = [];
  if (i.down_alert_sent_at) {
    parts.push("DOWN ✓");
    times.push(`Down alert: ${formatDateTime(i.down_alert_sent_at)}`);
  }
  if (reminders > 0) {
    parts.push(plural(reminders, "reminder"));
    times.push(`Last reminder: ${formatDateTime(i.last_reminder_sent_at)}`);
  }
  if (i.recovery_alert_sent_at) {
    parts.push("RECOVERED ✓");
    times.push(`Recovery alert: ${formatDateTime(i.recovery_alert_sent_at)}`);
  }
  if (parts.length === 0) return { text: "—", title: "No alerts sent" };
  return { text: parts.join(" · "), title: times.join("\n") };
}

/** Outages of one website; an open incident shows as "ongoing". */
export function MonitorIncidentsTable({ incidents }: { incidents: IncidentRecord[] }) {
  if (incidents.length === 0) return <p className="drawer__hint">No incidents recorded. </p>;
  return (
    <div className="tablewrap">
      <table className="table table--compact">
        <thead>
          <tr>
            <th>Started</th>
            <th>Ended</th>
            <th className="col-num">Duration</th>
            <th>Reason</th>
            <th>Closed by</th>
            <th>Alerts</th>
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => {
            const isOpen = i.resolved_at === null;
            const alerts = alertSummary(i);
            const seconds = i.duration_seconds ?? (Date.now() - new Date(i.started_at).getTime()) / 1000;
            return (
              <tr key={i.id} className={`row ${isOpen ? "row--expired" : ""}`}>
                <td className="mono-dim">{formatDateTime(i.started_at)}</td>
                <td className="mono-dim">{isOpen ? <span className="result result--down">ongoing</span> : formatDateTime(i.resolved_at)}</td>
                <td className="col-num">{formatDuration(seconds)}</td>
                <td className="cell-error" title={i.reason ?? undefined}>{i.reason ?? "—"}</td>
                <td className="mono-dim">{i.close_reason ? CLOSE_REASONS[i.close_reason] ?? i.close_reason : "—"}</td>
                <td className="mono-dim" title={alerts.title}>{alerts.text}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
