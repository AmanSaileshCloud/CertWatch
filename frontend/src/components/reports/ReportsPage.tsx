import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../../api";
import type { DowntimeEntry, ReportGranularity, ReportQuery, UptimeReport } from "../../types";
import { useCustomers } from "../../hooks/useCustomers";
import { formatDuration, formatPercent, plural, uptimeLevel } from "../../lib/format";
import { useToast } from "../Toast";
import { PageHeader } from "../layout/PageHeader";
import { DowntimeTable, UptimeTable, type UptimeRow } from "./ReportTables";
import {
  defaultGranularity,
  GRANULARITIES,
  isoDate,
  PRESETS,
  presetRange,
  type RangePreset,
} from "./reportRange";

type Tab = "websites" | "customers" | "downtime";

const TABS: { value: Tab; label: string }[] = [
  { value: "websites", label: "By website" },
  { value: "customers", label: "By customer" },
  { value: "downtime", label: "Downtime log" },
];

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Whole-range figures across every website in the report (time-weighted). */
function overall(report: UptimeReport) {
  let downtime = 0;
  let monitored = 0;
  let outages = 0;
  for (const c of report.customers) {
    downtime += c.total.downtime_seconds;
    monitored += c.total.monitored_seconds;
    outages += c.total.incidents;
  }
  const uptime = monitored > 0 ? Math.max(0, 100 * (1 - downtime / monitored)) : null;
  const belowTarget = report.monitors.filter((m) => m.total.uptime_pct !== null && m.total.uptime_pct < 99.9).length;
  return { uptime, downtime, outages, belowTarget };
}

export function ReportsPage() {
  const toast = useToast();
  const { customers } = useCustomers();
  const [preset, setPreset] = useState<RangePreset>("last30");
  const [[start, end], setRange] = useState<[string, string]>(() => presetRange("last30"));
  const [granularity, setGranularity] = useState<ReportGranularity>("day");
  const [customerId, setCustomerId] = useState<number | undefined>(undefined);
  const [tab, setTab] = useState<Tab>("websites");
  const [report, setReport] = useState<UptimeReport | null>(null);
  const [downtime, setDowntime] = useState<DowntimeEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const q: ReportQuery = useMemo(
    () => ({ start, end, granularity, customer_id: customerId }),
    [start, end, granularity, customerId],
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([api.uptimeReport(q), api.downtimeLog(q)])
      .then(([r, d]) => {
        if (!active) return;
        setReport(r);
        setDowntime(d);
        setError(null);
      })
      .catch((e) => active && setError(e instanceof ApiError ? e.message : "Failed to load the report"))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [q]);

  function choosePreset(p: RangePreset) {
    setPreset(p);
    if (p !== "custom") {
      setRange(presetRange(p));
      setGranularity(defaultGranularity(p));
    }
  }

  async function downloadCsv() {
    setExporting(true);
    try {
      const blob =
        tab === "downtime"
          ? await api.downtimeLogCsv(q)
          : await api.uptimeReportCsv(q, tab === "customers" ? "customer" : "monitor");
      const name = tab === "downtime" ? "downtime" : `uptime-by-${tab === "customers" ? "customer" : "website"}`;
      saveBlob(blob, `certwatch-${name}-${start}-to-${end}.csv`);
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Export failed", "error");
    } finally {
      setExporting(false);
    }
  }

  const websiteRows: UptimeRow[] = (report?.monitors ?? []).map((m) => ({
    key: `m${m.monitor_id}`,
    title: m.name,
    subtitle: [m.customer_name, m.group_name].filter(Boolean).join(" · ") || m.url,
    href: `#/websites/${m.monitor_id}`,
    figures: m.periods,
    total: m.total,
  }));
  const customerRows: UptimeRow[] = (report?.customers ?? []).map((c) => ({
    key: `c${c.customer_id ?? "none"}`,
    title: c.customer_name,
    subtitle: plural(c.monitors, "website"),
    figures: c.periods,
    total: c.total,
  }));
  const totals = report ? overall(report) : null;

  function body() {
    if (loading && !report) {
      return (
        <div className="skeleton">
          {[0, 1, 2].map((i) => (
            <div className="skeleton__row" key={i} style={{ animationDelay: `${i * 120}ms` }} />
          ))}
        </div>
      );
    }
    if (error) {
      return (
        <div className="empty empty--error" role="alert">
          <p className="empty__title">Couldn't build the report</p>
          <p className="empty__hint">{error}</p>
        </div>
      );
    }
    if (!report) return null;
    if (tab === "downtime") {
      return downtime.length === 0 ? (
        <div className="empty">
          <p className="empty__title">No downtime in this period</p>
          <p className="empty__hint">Every website stayed up from {start} to {end}.</p>
        </div>
      ) : (
        <DowntimeTable entries={downtime} />
      );
    }
    const rows = tab === "customers" ? customerRows : websiteRows;
    if (rows.length === 0) {
      return (
        <div className="empty">
          <p className="empty__title">No websites to report on</p>
          <p className="empty__hint">Add websites on the Websites page, or choose another customer.</p>
        </div>
      );
    }
    return (
      <UptimeTable
        rows={rows}
        periods={report.periods}
        firstColumn={tab === "customers" ? "Customer" : "Website"}
      />
    );
  }

  return (
    <>
      <PageHeader
        title="Reports"
        subtitle={`Uptime per website and customer · days in ${report?.timezone ?? "IST"}`}
        actions={
          <button className="btn btn--ghost" onClick={() => void downloadCsv()} disabled={exporting || !report}>
            {exporting ? "Exporting…" : "Download CSV"}
          </button>
        }
      />

      <div className="rpt-controls">
        <select className="select" value={preset} onChange={(e) => choosePreset(e.target.value as RangePreset)} aria-label="Date range">
          {PRESETS.map((p) => (
            <option key={p.value} value={p.value}>{p.label}</option>
          ))}
        </select>
        {preset === "custom" && (
          <>
            <input
              className="select"
              type="date"
              value={start}
              max={end}
              onChange={(e) => e.target.value && setRange([e.target.value, end])}
              aria-label="From"
            />
            <span className="rpt-controls__to">to</span>
            <input
              className="select"
              type="date"
              value={end}
              min={start}
              max={isoDate(new Date())}
              onChange={(e) => e.target.value && setRange([start, e.target.value])}
              aria-label="To"
            />
          </>
        )}
        <select className="select" value={granularity} onChange={(e) => setGranularity(e.target.value as ReportGranularity)} aria-label="Breakdown">
          {GRANULARITIES.map((g) => (
            <option key={g.value} value={g.value}>{g.label}</option>
          ))}
        </select>
        <select
          className="select"
          value={customerId ?? ""}
          onChange={(e) => setCustomerId(e.target.value ? Number(e.target.value) : undefined)}
          aria-label="Customer"
        >
          <option value="">All customers</option>
          {customers.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        {loading && report && <span className="rpt-controls__busy">Updating…</span>}
      </div>

      {totals && !error && (
        <div className="stats">
          <div className="stat">
            <span className="stat__label">Overall uptime</span>
            <span className={`stat__value uptime--${uptimeLevel(totals.uptime)}`}>{formatPercent(totals.uptime)}</span>
          </div>
          <div className="stat">
            <span className="stat__label">Total downtime</span>
            <span className="stat__value">{totals.downtime ? formatDuration(totals.downtime) : "None"}</span>
          </div>
          <div className="stat">
            <span className="stat__label">Outages</span>
            <span className="stat__value">{totals.outages}</span>
          </div>
          <div className="stat">
            <span className="stat__label">Websites below 99.9%</span>
            <span className={`stat__value ${totals.belowTarget ? "uptime--poor" : ""}`}>
              {totals.belowTarget} / {report?.monitors.length ?? 0}
            </span>
          </div>
        </div>
      )}

      <div className="rpt-tabs" role="tablist" aria-label="Report view">
        {TABS.map((t) => (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={tab === t.value}
            className={`rpt-tabs__tab ${tab === t.value ? "is-active" : ""}`}
            onClick={() => setTab(t.value)}
          >
            {t.label}
            {t.value === "downtime" && downtime.length > 0 && <span className="rpt-tabs__count">{downtime.length}</span>}
          </button>
        ))}
      </div>

      {body()}

      <p className="rpt-footnote">
        Uptime counts confirmed outages only (several failed checks in a row, 3 by default), from the first failed check until the site
        recovered, against the time each website was monitored. 99.9% ≈ 43 minutes of downtime a month.
      </p>
    </>
  );
}
