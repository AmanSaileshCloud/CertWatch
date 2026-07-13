import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, ApiError } from "../api";
import type { DomainRecord, Status } from "../types";
import { useAuth } from "../auth/AuthContext";
import { useToast } from "./Toast";
import { AddDomainForm } from "./AddDomainForm";
import { AuroraBackground } from "./AuroraBackground";
import { DomainTable } from "./DomainTable";
import { DomainDetail } from "./DomainDetail";
import { SummaryBar } from "./SummaryBar";
import { HealthRing } from "./HealthRing";
import { ForecastWidget } from "./ForecastWidget";
import { SearchFilter } from "./SearchFilter";
import { ExportMenu } from "./ExportMenu";
import { BulkImportModal } from "./BulkImportModal";
import { UsersAdmin } from "./UsersAdmin";
import { ConfirmDialog } from "./ConfirmDialog";
import { ChangePasswordModal } from "./ChangePasswordModal";
import type { ThemePreference } from "../hooks/useTheme";
import logoUrl from "../assets/WorkmatesLogo.png";

interface DashboardProps {
  theme: "dark" | "light";
  preference: ThemePreference;
  onToggleTheme: () => void;
}

function ThemeCycleButton({ preference, onCycle }: { preference: ThemePreference; onCycle: () => void }) {
  const label = preference === "dark" ? "Dark mode" : preference === "light" ? "Light mode" : "Auto (OS)";
  return (
    <button
      className="theme-cycle btn btn--ghost"
      onClick={onCycle}
      title={`${label} — click to change`}
      aria-label="Cycle theme"
    >
      {preference === "dark" && (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      )}
      {preference === "light" && (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
        </svg>
      )}
      {preference === "auto" && (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
          <line x1="8" y1="21" x2="16" y2="21" />
          <line x1="12" y1="17" x2="12" y2="21" />
        </svg>
      )}
    </button>
  );
}

export function Dashboard({ preference, onToggleTheme }: DashboardProps) {
  const { user, logout } = useAuth();
  const toast = useToast();
  const [domains, setDomains] = useState<DomainRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [confirmBulk, setConfirmBulk] = useState(false);
  const [showChangePw, setShowChangePw] = useState(false);
  const [highlightKey, setHighlightKey] = useState<string | null>(null);
  const [selected, setSelected] = useState<DomainRecord | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [filterQuery, setFilterQuery] = useState("");
  const [filterStatus, setFilterStatus] = useState<Status | "all">("all");
  const [showBulkImport, setShowBulkImport] = useState(false);
  const [showUsers, setShowUsers] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setDomains(await api.listDomains());
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to load domains", "error");
    } finally {
      setLoading(false);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { void refresh(); }, [refresh]);

  const filteredDomains = useMemo(() => {
    return domains.filter((d) => {
      const matchQuery = filterQuery === "" || d.host.toLowerCase().includes(filterQuery.toLowerCase());
      const matchStatus = filterStatus === "all" || d.status === filterStatus;
      return matchQuery && matchStatus;
    });
  }, [domains, filterQuery, filterStatus]);

  // Clear selection when filter changes
  useEffect(() => { setSelectedKeys(new Set()); }, [filterQuery, filterStatus]);

  function handleToggleSelect(key: string) {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function handleSelectAll() {
    if (selectedKeys.size === filteredDomains.length) {
      setSelectedKeys(new Set());
    } else {
      setSelectedKeys(new Set(filteredDomains.map((d) => d.domain)));
    }
  }

  async function handleDeleteSelected() {
    const keys = [...selectedKeys];
    setSelectedKeys(new Set());
    await Promise.allSettled(keys.map((k) => api.deleteDomain(k)));
    await refresh();
    toast.show(`Removed ${keys.length} domain${keys.length !== 1 ? "s" : ""}`, "info");
  }

  async function handleAdd(domain: string, port: number) {
    setBusy(true);
    try {
      const created = await api.addDomain(domain, port);
      await refresh();
      setHighlightKey(created.domain);
      window.setTimeout(() => setHighlightKey(null), 1400);
      toast.show(`${domain} is now being monitored`, "success");
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Could not add domain", "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(key: string) {
    setDeletingKey(key);
    try {
      await api.deleteDomain(key);
      await refresh();
      toast.show("Domain removed", "info");
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Could not delete domain", "error");
    } finally {
      setDeletingKey(null);
    }
  }

  return (
    <>
      <AuroraBackground />
      <div className="app">
        <div className="scanline" aria-hidden="true" />
        <header className="header">
          <div className="brand">
            <motion.div
              className="brand__logo"
              initial={{ opacity: 0, x: -28, scale: 0.85 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              transition={{ type: "spring", stiffness: 320, damping: 22, delay: 0.08 }}
            >
              <div className="brand__logo-icon">
                <img src={logoUrl} alt="" aria-hidden="true" />
              </div>
            </motion.div>
            <span className="brand__divider" aria-hidden="true" />
            <div className="brand__text">
              <h1>CERT<span className="brand__accent">WATCH</span></h1>
              <p className="brand__sub">TLS expiry surveillance · by Workmates</p>
            </div>
          </div>

          <div className="header__actions">
            <ThemeCycleButton preference={preference} onCycle={onToggleTheme} />

            <button
              className="btn btn--ghost"
              onClick={() => setShowBulkImport(true)}
              title="Import multiple domains at once"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ marginRight: 5, verticalAlign: "middle" }}>
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
              Import
            </button>

            {user?.role === "admin" && (
              <button
                className="btn btn--ghost"
                onClick={() => setShowUsers(true)}
                title="Manage users"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ marginRight: 5, verticalAlign: "middle" }}>
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                </svg>
                Users
              </button>
            )}

            <ExportMenu domains={domains} isAdmin={user?.role === "admin"} />

            <div className="usermenu">
              <span className="usermenu__avatar" aria-hidden="true">
                {(user?.username ?? "?").slice(0, 1).toUpperCase()}
              </span>
              <span className="usermenu__name">{user?.username}</span>
              <button
                className="btn btn--ghost btn--icon"
                onClick={() => setShowChangePw(true)}
                title="Change password"
                aria-label="Change password"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
                </svg>
              </button>
              <button className="btn btn--ghost usermenu__logout" onClick={logout} title="Sign out">
                Sign out
              </button>
            </div>
          </div>
        </header>

        <main className="main">
          {/* Summary + Health Ring */}
          <div className="overview">
            <SummaryBar domains={domains} filter={filterStatus} onFilter={setFilterStatus} />
            <HealthRing domains={domains} />
          </div>

          {/* 7-day forecast */}
          <AnimatePresence>
            {!loading && (
              <ForecastWidget domains={domains} onSelect={setSelected} />
            )}
          </AnimatePresence>

          {/* Add form + search + export row */}
          <AddDomainForm onAdd={handleAdd} busy={busy} />
          <SearchFilter
            query={filterQuery}
            status={filterStatus}
            total={domains.length}
            filtered={filteredDomains.length}
            onQuery={setFilterQuery}
            onStatus={setFilterStatus}
          />

          {/* Bulk action bar */}
          <AnimatePresence>
            {selectedKeys.size > 0 && (
              <motion.div
                className="bulkbar"
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
              >
                <span className="bulkbar__count">{selectedKeys.size} selected</span>
                <button className="btn btn--ghost bulkbar__deselect" onClick={() => setSelectedKeys(new Set())}>
                  Deselect all
                </button>
                <button className="btn bulkbar__delete" onClick={() => setConfirmBulk(true)}>
                  Delete {selectedKeys.size} domain{selectedKeys.size !== 1 ? "s" : ""}
                </button>
              </motion.div>
            )}
          </AnimatePresence>

          {loading ? (
            <div className="skeleton">
              {[0, 1, 2].map((i) => (
                <div className="skeleton__row" key={i} style={{ animationDelay: `${i * 120}ms` }} />
              ))}
            </div>
          ) : filteredDomains.length === 0 && domains.length === 0 ? (
            <motion.div className="empty" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}>
              <div className="empty__icon" aria-hidden="true">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                  <circle cx="12" cy="11" r="3" />
                </svg>
              </div>
              <p className="empty__title">No domains under watch</p>
              <p className="empty__hint">Add a hostname above to start monitoring its TLS certificate.</p>
              <ol className="empty__steps">
                <li>Type a domain in the <strong>Add domain</strong> form above</li>
                <li>Click <strong>Add</strong> — it appears in the table instantly</li>
                <li>Hit <strong>Run check now</strong> to fetch live cert data</li>
              </ol>
            </motion.div>
          ) : filteredDomains.length === 0 ? (
            <motion.div className="empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <p className="empty__title">No matches</p>
              <p className="empty__hint">Try a different search term or status filter.</p>
              <button className="btn btn--ghost" onClick={() => { setFilterQuery(""); setFilterStatus("all"); }}>
                Clear filters
              </button>
            </motion.div>
          ) : (
            <DomainTable
              domains={filteredDomains}
              selectedKeys={selectedKeys}
              onToggleSelect={handleToggleSelect}
              onSelectAll={handleSelectAll}
              onDelete={setConfirmDelete}
              onSelect={setSelected}
              deletingKey={deletingKey}
              running={false}
              highlightKey={highlightKey}
            />
          )}
        </main>

        <footer className="footer">
          <span className="footer__brand">© Workmates</span>
          <span>Thresholds 30 · 14 · 7 · 1 days</span>
          <span>Local mode · console notifier</span>
        </footer>

        <AnimatePresence>
          {confirmDelete && (
            <ConfirmDialog
              title="Stop monitoring?"
              message={<>Remove <strong>{confirmDelete.replace(/:443$/, "")}</strong> from the dashboard? You can add it again anytime.</>}
              confirmLabel="Remove"
              onCancel={() => setConfirmDelete(null)}
              onConfirm={() => {
                const key = confirmDelete;
                setConfirmDelete(null);
                void handleDelete(key);
              }}
            />
          )}
        </AnimatePresence>

        <AnimatePresence>
          {confirmBulk && (
            <ConfirmDialog
              title="Stop monitoring?"
              message={<>Remove <strong>{selectedKeys.size} domain{selectedKeys.size !== 1 ? "s" : ""}</strong> from the dashboard? This can't be undone in one click.</>}
              confirmLabel={`Remove ${selectedKeys.size}`}
              onCancel={() => setConfirmBulk(false)}
              onConfirm={() => {
                setConfirmBulk(false);
                void handleDeleteSelected();
              }}
            />
          )}
        </AnimatePresence>

        <AnimatePresence>
          {showChangePw && <ChangePasswordModal onClose={() => setShowChangePw(false)} />}
        </AnimatePresence>

        <AnimatePresence>
          {showUsers && (
            <UsersAdmin onClose={() => setShowUsers(false)} currentUser={user?.username} />
          )}
        </AnimatePresence>

        <AnimatePresence>
          {showBulkImport && (
            <BulkImportModal
              onClose={() => setShowBulkImport(false)}
              onImported={(added) => {
                void refresh();
                if (added.length > 0) {
                  setHighlightKey(added[added.length - 1].domain);
                  window.setTimeout(() => setHighlightKey(null), 1400);
                  toast.show(`${added.length} domain${added.length !== 1 ? "s" : ""} imported`, "success");
                }
              }}
            />
          )}
        </AnimatePresence>

        <AnimatePresence>
          {selected && (
            <DomainDetail
              key={selected.domain}
              record={selected}
              onClose={() => setSelected(null)}
              onSaved={(updated) => { setSelected(updated); void refresh(); }}
            />
          )}
        </AnimatePresence>
      </div>
    </>
  );
}
