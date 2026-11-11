import type { KeyboardEvent } from "react";
import type { MonitorRecord } from "../../types";
import { formatMs, formatPercent, formatRelativeTime, uptimeLevel } from "../../lib/format";
import { MonitorStatusBadge } from "./MonitorStatusBadge";

type Props = {
  monitors: MonitorRecord[];
  onOpen: (id: number) => void;
  showGroup?: boolean;
};

function GroupCell({ monitor }: { monitor: MonitorRecord }) {
  if (!monitor.group_name) return <span className="muted">—</span>;
  return (
    <>
      <span className="mon__group">{monitor.group_name}</span>
      {monitor.customer_name && <span className="mon__sub">{monitor.customer_name}</span>}
    </>
  );
}

export function MonitorTable({ monitors, onOpen, showGroup = true }: Props) {
  function onRowKey(e: KeyboardEvent<HTMLTableRowElement>, id: number) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen(id);
    }
  }

  return (
    <div className="tablewrap">
      <table className="table montable">
        <thead>
          <tr>
            <th className="col-mstatus">Status</th>
            <th>Website</th>
            {showGroup && <th className="col-mgroup">Group</th>}
            <th className="col-num">Response</th>
            <th className="col-num">Uptime 24h</th>
            <th className="col-checked">Last checked</th>
          </tr>
        </thead>
        <tbody>
          {monitors.map((m) => (
            <tr
              key={m.id}
              className={`row row--clickable ${m.status === "down" ? "row--expired" : ""}`}
              tabIndex={0}
              onClick={() => onOpen(m.id)}
              onKeyDown={(e) => onRowKey(e, m.id)}
              aria-label={`${m.name}, ${m.status}. Open details`}
            >
              <td className="col-mstatus">
                <MonitorStatusBadge status={m.status} />
              </td>
              <td>
                <span className="domain">{m.name}</span>
                <span className="mon__sub mon__url" title={m.url}>{m.url}</span>
                {m.status === "down" && m.last_error && (
                  <span className="domain__err" title={m.last_error}>{m.last_error}</span>
                )}
              </td>
              {showGroup && (
                <td className="col-mgroup">
                  <GroupCell monitor={m} />
                </td>
              )}
              <td className="col-num">{formatMs(m.last_response_ms)}</td>
              <td className="col-num">
                <span className={`uptime uptime--${uptimeLevel(m.uptime_24h)}`}>{formatPercent(m.uptime_24h)}</span>
              </td>
              <td className="col-checked" title={m.last_checked_at ? new Date(m.last_checked_at).toLocaleString() : undefined}>
                {formatRelativeTime(m.last_checked_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
