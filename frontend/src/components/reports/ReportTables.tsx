import type { DowntimeEntry, ReportFigures, ReportPeriod } from "../../types";
import { formatDateTime, formatDuration, formatMs, formatPercent, uptimeLevel } from "../../lib/format";

const CLOSE_REASONS: Record<string, string> = {
  recovered: "Recovered",
  paused: "Paused",
  edited: "Settings edited",
};

function Uptime({ value }: { value: number | null }) {
  return <span className={`uptime uptime--${uptimeLevel(value)}`}>{formatPercent(value)}</span>;
}

/** One small coloured cell per period; hover shows the exact figures. */
function PeriodStrip({ periods, figures }: { periods: ReportPeriod[]; figures: ReportFigures[] }) {
  return (
    <div className="rpt-strip" role="img" aria-label="Uptime per period">
      {figures.map((f, i) => (
        <span
          key={periods[i].key}
          className={`rpt-strip__cell rpt-strip__cell--${uptimeLevel(f.uptime_pct)}`}
          title={`${periods[i].label}: ${formatPercent(f.uptime_pct)}${f.downtime_seconds ? ` · down ${formatDuration(f.downtime_seconds)}` : ""}`}
        />
      ))}
    </div>
  );
}

export interface UptimeRow {
  key: string;
  title: string;
  subtitle: string | null;
  href?: string;
  figures: ReportFigures[];
  total: ReportFigures;
}

/** Websites or customers: whole-range totals plus a per-period strip. */
export function UptimeTable({ rows, periods, firstColumn }: { rows: UptimeRow[]; periods: ReportPeriod[]; firstColumn: string }) {
  return (
    <div className="tablewrap">
      <table className="table table--compact rpt-table">
        <thead>
          <tr>
            <th>{firstColumn}</th>
            <th className="col-num">Uptime</th>
            <th className="col-num">Downtime</th>
            <th className="col-num">Outages</th>
            <th className="col-num">Avg response</th>
            {periods.length > 1 && <th>Per period</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="row">
              <td>
                {r.href ? <a className="rpt-name" href={r.href}>{r.title}</a> : <span className="rpt-name">{r.title}</span>}
                {r.subtitle && <span className="rpt-sub">{r.subtitle}</span>}
              </td>
              <td className="col-num"><Uptime value={r.total.uptime_pct} /></td>
              <td className="col-num mono-dim">{r.total.downtime_seconds ? formatDuration(r.total.downtime_seconds) : "—"}</td>
              <td className="col-num mono-dim">{r.total.incidents || "—"}</td>
              <td className="col-num mono-dim">{formatMs(r.total.avg_response_ms)}</td>
              {periods.length > 1 && <td><PeriodStrip periods={periods} figures={r.figures} /></td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DowntimeTable({ entries }: { entries: DowntimeEntry[] }) {
  return (
    <div className="tablewrap">
      <table className="table table--compact rpt-table">
        <thead>
          <tr>
            <th>Website</th>
            <th>Started</th>
            <th>Ended</th>
            <th className="col-num">Downtime</th>
            <th>Reason</th>
            <th>How it ended</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => {
            const open = e.resolved_at === null;
            return (
              <tr key={e.incident_id} className={`row ${open ? "row--expired" : ""}`}>
                <td>
                  <a className="rpt-name" href={`#/websites/${e.monitor_id}`}>{e.monitor_name}</a>
                  <span className="rpt-sub">{[e.customer_name, e.group_name].filter(Boolean).join(" · ") || e.url}</span>
                </td>
                <td className="mono-dim">{formatDateTime(e.started_at)}</td>
                <td className="mono-dim">{open ? <span className="result result--down">still down</span> : formatDateTime(e.resolved_at)}</td>
                <td className="col-num">{formatDuration(e.downtime_seconds)}</td>
                <td className="cell-error" title={e.reason ?? undefined}>{e.reason ?? "—"}</td>
                <td className="mono-dim">{e.close_reason ? CLOSE_REASONS[e.close_reason] ?? e.close_reason : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
