import { useCallback, useEffect, useState } from "react";
import { AnimatePresence } from "motion/react";
import { api, ApiError } from "../../api";
import type { CheckRecord, IncidentRecord, MonitorRecord } from "../../types";
import { navigate } from "../../hooks/useHashRoute";
import { useAutoRefresh } from "../../hooks/useAutoRefresh";
import { formatDateTime, formatMs, formatPercent, formatRelativeTime, uptimeLevel } from "../../lib/format";
import { useToast } from "../Toast";
import { ConfirmDialog } from "../ConfirmDialog";
import { PageHeader } from "../layout/PageHeader";
import { MonitorAlertsCard } from "./MonitorAlertsCard";
import { MonitorChecksTable } from "./MonitorChecksTable";
import { MonitorIncidentsTable } from "./MonitorIncidentsTable";
import { MonitorStatusBadge } from "./MonitorStatusBadge";
import { MonitorFormModal } from "./MonitorFormModal";
import { ResponseTimeChart } from "./ResponseTimeChart";

const SERIES_HOURS = 24;
const CHECK_LIMIT = 50;

interface MonitorData {
  monitor: MonitorRecord;
  series: CheckRecord[];
  checks: CheckRecord[];
  incidents: IncidentRecord[];
}

async function loadMonitorData(id: number): Promise<MonitorData> {
  const [monitor, series, checks, incidents] = await Promise.all([
    api.getMonitor(id),
    api.getSeries(id, SERIES_HOURS),
    api.listChecks(id, CHECK_LIMIT),
    api.listMonitorIncidents(id),
  ]);
  return { monitor, series, checks, incidents };
}

function averageResponse(series: CheckRecord[]): number | null {
  const ms = series.filter((c) => c.is_up && c.response_ms !== null).map((c) => c.response_ms as number);
  return ms.length ? ms.reduce((a, b) => a + b, 0) / ms.length : null;
}

function StatTile({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="stat">
      <span className={`stat__value ${tone ? `uptime--${tone}` : ""}`}>{value}</span>
      <span className="stat__label">{label}</span>
    </div>
  );
}

function BackLink() {
  return (
    <a className="backlink" href="#/websites">
      <span aria-hidden="true">←</span> All websites
    </a>
  );
}

export function MonitorDetailPage({ monitorId }: { monitorId: number }) {
  const toast = useToast();
  const [data, setData] = useState<MonitorData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setData(await loadMonitorData(monitorId));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load this website");
    }
  }, [monitorId]);

  useEffect(() => { void refresh(); }, [refresh]);
  useAutoRefresh(() => void refresh());

  async function runAction(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    try {
      await action();
      toast.show(success, "success");
      await refresh();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Something went wrong", "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    setBusy(true);
    try {
      await api.deleteMonitor(monitorId);
      toast.show("Website removed", "info");
      navigate("/websites");
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Could not delete website", "error");
      setBusy(false);
    }
  }

  if (!data) {
    return (
      <>
        <BackLink />
        {error ? (
          <div className="empty empty--error" role="alert">
            <p className="empty__title">Couldn't load this website</p>
            <p className="empty__hint">{error}</p>
            <button className="btn btn--ghost" onClick={() => void refresh()}>Try again</button>
          </div>
        ) : (
          <div className="skeleton">
            {[0, 1, 2].map((i) => <div className="skeleton__row" key={i} style={{ animationDelay: `${i * 120}ms` }} />)}
          </div>
        )}
      </>
    );
  }

  const { monitor, series, checks, incidents } = data;
  const avg = averageResponse(series);

  const actions = (
    <>
      <button className="btn btn--ghost" onClick={() => setShowEdit(true)} disabled={busy}>Edit</button>
      {monitor.is_paused ? (
        <button className="btn btn--ghost" disabled={busy} onClick={() => void runAction(() => api.resumeMonitor(monitor.id), "Monitoring resumed")}>
          Resume
        </button>
      ) : (
        <button className="btn btn--ghost" disabled={busy} onClick={() => void runAction(() => api.pauseMonitor(monitor.id), "Monitoring paused")}>
          Pause
        </button>
      )}
      <button className="btn btn--danger-ghost" onClick={() => setConfirmDelete(true)} disabled={busy}>Delete</button>
    </>
  );

  return (
    <>
      <BackLink />
      <PageHeader
        title={<span className="mondetail__title">{monitor.name} <MonitorStatusBadge status={monitor.status} /></span>}
        subtitle={
          <span className="mondetail__meta">
            <a href={monitor.url} target="_blank" rel="noopener noreferrer" className="mondetail__url">{monitor.url}</a>
            {monitor.group_id !== null && (
              <a className="mondetail__group" href={`#/groups/${monitor.group_id}`}>
                {monitor.customer_name ? `${monitor.customer_name} · ` : ""}{monitor.group_name}
              </a>
            )}
          </span>
        }
        actions={actions}
      />

      {error && <div className="banner banner--error" role="alert">Refresh failed: {error}</div>}

      {monitor.status === "down" && (
        <div className="banner banner--error mondetail__down" role="status">
          <span>
            <strong>Down</strong>
            {monitor.down_since && <> since {formatDateTime(monitor.down_since)} ({formatRelativeTime(monitor.down_since)})</>}
            {monitor.last_error && <> — {monitor.last_error}</>}
          </span>
        </div>
      )}

      <div className="stats">
        <StatTile label="Uptime 24h" value={formatPercent(monitor.uptime_24h)} tone={uptimeLevel(monitor.uptime_24h)} />
        <StatTile label="Last response" value={formatMs(monitor.last_response_ms)} />
        <StatTile label="Avg response 24h" value={formatMs(avg)} />
        <StatTile label="Checks 24h" value={String(series.length)} />
      </div>

      <section className="panel">
        <h3 className="panel__title">Response time · last {SERIES_HOURS}h</h3>
        <ResponseTimeChart checks={series} hours={SERIES_HOURS} intervalSeconds={monitor.interval_seconds} />
      </section>

      <MonitorAlertsCard monitor={monitor} />

      <section className="panel">
        <h3 className="panel__title">Incidents</h3>
        <MonitorIncidentsTable incidents={incidents} />
      </section>

      <section className="panel">
        <h3 className="panel__title">Recent checks</h3>
        <p className="drawer__hint panel__hint">
          Every {monitor.interval_seconds / 60} min · timeout {monitor.timeout_seconds}s · expects HTTP {monitor.expected_status}
          {monitor.keyword && <> · keyword “{monitor.keyword}”</>} · last checked {formatRelativeTime(monitor.last_checked_at)}
        </p>
        <MonitorChecksTable checks={checks} />
      </section>

      <AnimatePresence>
        {showEdit && (
          <MonitorFormModal
            monitor={monitor}
            onClose={() => setShowEdit(false)}
            onSaved={() => {
              toast.show("Website updated", "success");
              void refresh();
            }}
          />
        )}
      </AnimatePresence>

      <AnimatePresence>
        {confirmDelete && (
          <ConfirmDialog
            title="Delete website?"
            message={<>Stop monitoring <strong>{monitor.name}</strong> and delete its check history? This can't be undone.</>}
            confirmLabel="Delete"
            onCancel={() => setConfirmDelete(false)}
            onConfirm={() => {
              setConfirmDelete(false);
              void handleDelete();
            }}
          />
        )}
      </AnimatePresence>
    </>
  );
}
