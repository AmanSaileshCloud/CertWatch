import type { CheckRecord } from "../../types";
import { formatDateTime, formatMs } from "../../lib/format";

/** The most recent checks of one website, newest first. */
export function MonitorChecksTable({ checks }: { checks: CheckRecord[] }) {
  if (checks.length === 0) return <p className="drawer__hint">No checks yet — the first one runs shortly after adding.</p>;
  return (
    <div className="tablewrap">
      <table className="table table--compact">
        <thead>
          <tr>
            <th>Checked</th>
            <th>Result</th>
            <th className="col-num">HTTP</th>
            <th className="col-num">Response</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {checks.map((c) => (
            <tr key={c.checked_at} className={`row ${c.is_up ? "" : "row--expired"}`}>
              <td className="mono-dim">{formatDateTime(c.checked_at)}</td>
              <td>
                <span className={`result result--${c.is_up ? "up" : "down"}`}>{c.is_up ? "Up" : "Down"}</span>
              </td>
              <td className="col-num">{c.status_code ?? "—"}</td>
              <td className="col-num">{formatMs(c.response_ms)}</td>
              <td className="cell-error" title={c.error ?? undefined}>{c.error ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
