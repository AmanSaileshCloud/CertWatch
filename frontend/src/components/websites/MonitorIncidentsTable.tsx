import type { IncidentRecord } from "../../types";
import { formatDateTime, formatDuration } from "../../lib/format";

const CLOSE_REASONS: Record<string, string> = {
  recovered: "Recovered",
  paused: "Paused",
  edited: "Settings edited",
};

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
          </tr>
        </thead>
        <tbody>
          {incidents.map((i) => {
            const isOpen = i.resolved_at === null;
            const seconds = i.duration_seconds ?? (Date.now() - new Date(i.started_at).getTime()) / 1000;
            return (
              <tr key={i.id} className={`row ${isOpen ? "row--expired" : ""}`}>
                <td className="mono-dim">{formatDateTime(i.started_at)}</td>
                <td className="mono-dim">{isOpen ? <span className="result result--down">ongoing</span> : formatDateTime(i.resolved_at)}</td>
                <td className="col-num">{formatDuration(seconds)}</td>
                <td className="cell-error" title={i.reason ?? undefined}>{i.reason ?? "—"}</td>
                <td className="mono-dim">{i.close_reason ? CLOSE_REASONS[i.close_reason] ?? i.close_reason : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
