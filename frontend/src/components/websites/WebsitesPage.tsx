import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, ApiError } from "../../api";
import type { MonitorRecord, MonitorStatus } from "../../types";
import { navigate } from "../../hooks/useHashRoute";
import { useAutoRefresh } from "../../hooks/useAutoRefresh";
import { useToast } from "../Toast";
import { PageHeader } from "../layout/PageHeader";
import { useGroups } from "../groups/GroupPicker";
import { MonitorTable } from "./MonitorTable";
import { MonitorFormModal } from "./MonitorFormModal";

type StatusFilter = MonitorStatus | "all";
/** "all" groups, websites with "none", or a group id. */
type GroupFilter = "all" | "none" | number;

const COUNTERS: { status: MonitorStatus; label: string }[] = [
  { status: "up", label: "Up" },
  { status: "down", label: "Down" },
  { status: "paused", label: "Paused" },
  { status: "unknown", label: "Unknown" },
];

function StatusCounters({
  monitors,
  filter,
  onFilter,
}: {
  monitors: MonitorRecord[];
  filter: StatusFilter;
  onFilter: (f: StatusFilter) => void;
}) {
  const counts = useMemo(() => {
    const c: Record<MonitorStatus, number> = { up: 0, down: 0, paused: 0, unknown: 0 };
    monitors.forEach((m) => c[m.status]++);
    return c;
  }, [monitors]);

  return (
    <div className="summary mon-summary">
      <button
        type="button"
        className={`summary__total ${filter === "all" ? "chip--selected" : ""}`}
        onClick={() => onFilter("all")}
        aria-pressed={filter === "all"}
        title="Show all"
      >
        <span className="summary__total-num">{monitors.length}</span>
        <span className="summary__total-label">websites</span>
      </button>
      <div className="summary__chips">
        {COUNTERS.map(({ status, label }) => {
          const selected = filter === status;
          const alarming = status === "down" && counts.down > 0;
          return (
            <button
              key={status}
              type="button"
              className={`chip chip--m-${status} ${alarming ? "chip--active chip--expired" : ""} ${selected ? "chip--selected" : ""}`}
              onClick={() => onFilter(selected ? "all" : status)}
              aria-pressed={selected}
              title={`Show ${label.toLowerCase()} only`}
            >
              <span className="chip__count">{counts[status]}</span>
              <span className="chip__label">{label}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function GroupFilterSelect({ value, onChange }: { value: GroupFilter; onChange: (v: GroupFilter) => void }) {
  const { groups } = useGroups();

  function parse(raw: string): GroupFilter {
    return raw === "all" || raw === "none" ? raw : Number(raw);
  }

  return (
    <select className="select" value={String(value)} onChange={(e) => onChange(parse(e.target.value))} aria-label="Filter by group">
      <option value="all">All groups</option>
      <option value="none">No group</option>
      {(groups ?? []).map((g) => (
        <option key={g.id} value={g.id}>{g.customer_name} · {g.name}</option>
      ))}
    </select>
  );
}

function matchesGroup(m: MonitorRecord, group: GroupFilter): boolean {
  if (group === "all") return true;
  if (group === "none") return m.group_id === null;
  return m.group_id === group;
}

function matchesQuery(m: MonitorRecord, query: string): boolean {
  const q = query.trim().toLowerCase();
  return q === "" || m.name.toLowerCase().includes(q) || m.url.toLowerCase().includes(q);
}

export function WebsitesPage() {
  const toast = useToast();
  const [monitors, setMonitors] = useState<MonitorRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [groupFilter, setGroupFilter] = useState<GroupFilter>("all");
  const [query, setQuery] = useState("");
  const [showAdd, setShowAdd] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setMonitors(await api.listMonitors());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load websites");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  useAutoRefresh(() => void refresh());

  const visible = useMemo(
    () =>
      monitors.filter(
        (m) =>
          (statusFilter === "all" || m.status === statusFilter) &&
          matchesGroup(m, groupFilter) &&
          matchesQuery(m, query),
      ),
    [monitors, statusFilter, groupFilter, query],
  );

  const isFiltering = statusFilter !== "all" || groupFilter !== "all" || query !== "";

  function clearFilters() {
    setStatusFilter("all");
    setGroupFilter("all");
    setQuery("");
  }

  function body() {
    if (loading) {
      return (
        <div className="skeleton">
          {[0, 1, 2].map((i) => (
            <div className="skeleton__row" key={i} style={{ animationDelay: `${i * 120}ms` }} />
          ))}
        </div>
      );
    }
    if (error && monitors.length === 0) {
      return (
        <div className="empty empty--error" role="alert">
          <p className="empty__title">Couldn't load websites</p>
          <p className="empty__hint">{error}</p>
          <button className="btn btn--ghost" onClick={() => { setLoading(true); void refresh(); }}>Try again</button>
        </div>
      );
    }
    if (monitors.length === 0) {
      return (
        <motion.div className="empty" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}>
          <div className="empty__icon" aria-hidden="true">
            <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
            </svg>
          </div>
          <p className="empty__title">No websites monitored yet</p>
          <p className="empty__hint">Add a URL and CertWatch will check that it responds, every 1–10 minutes.</p>
          <button className="btn btn--primary" onClick={() => setShowAdd(true)}>
            <span className="btn__plus" aria-hidden="true">+</span> Add website
          </button>
        </motion.div>
      );
    }
    if (visible.length === 0) {
      return (
        <div className="empty">
          <p className="empty__title">No matches</p>
          <p className="empty__hint">Try a different search, status or group.</p>
          <button className="btn btn--ghost" onClick={clearFilters}>Clear filters</button>
        </div>
      );
    }
    return <MonitorTable monitors={visible} onOpen={(id) => navigate(`/websites/${id}`)} />;
  }

  return (
    <>
      <PageHeader
        title="Websites"
        subtitle="Uptime and response time · refreshes every 30 seconds"
        actions={
          <button className="btn btn--primary" onClick={() => setShowAdd(true)}>
            <span className="btn__plus" aria-hidden="true">+</span>
            Add website
          </button>
        }
      />

      <StatusCounters monitors={monitors} filter={statusFilter} onFilter={setStatusFilter} />

      <div className="searchbar">
        <div className="searchbar__input-wrap">
          <svg className="searchbar__icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            className="searchbar__input"
            type="text"
            placeholder="Search name or URL…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search websites"
          />
          {query && <button className="searchbar__clear" onClick={() => setQuery("")} aria-label="Clear search">✕</button>}
        </div>
        <GroupFilterSelect value={groupFilter} onChange={setGroupFilter} />
        {isFiltering && <span className="searchbar__count">{visible.length} / {monitors.length}</span>}
      </div>

      {error && monitors.length > 0 && (
        <div className="banner banner--error" role="alert">Refresh failed: {error}</div>
      )}

      {body()}

      <AnimatePresence>
        {showAdd && (
          <MonitorFormModal
            onClose={() => setShowAdd(false)}
            onSaved={(m) => {
              toast.show(`${m.name} is now being monitored`, "success");
              void refresh();
            }}
          />
        )}
      </AnimatePresence>
    </>
  );
}
