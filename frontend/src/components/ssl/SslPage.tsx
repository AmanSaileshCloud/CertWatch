import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, ApiError } from "../../api";
import type { DomainRecord, Status } from "../../types";
import { useAuth } from "../../auth/AuthContext";
import { navigate } from "../../hooks/useHashRoute";
import { plural } from "../../lib/format";
import { useToast } from "../Toast";
import { AddDomainModal } from "../AddDomainModal";
import { DomainTable } from "../DomainTable";
import { DomainDetail } from "../DomainDetail";
import { SummaryBar } from "../SummaryBar";
import { HealthRing } from "../HealthRing";
import { ForecastWidget } from "../ForecastWidget";
import { SearchFilter } from "../SearchFilter";
import { BulkImportModal } from "../BulkImportModal";
import { ConfirmDialog } from "../ConfirmDialog";
import { PageHeader } from "../layout/PageHeader";
import { useGroups } from "../groups/GroupPicker";
import { SslEmptyState } from "./SslEmptyState";
import { SslBulkBar, SslToolbar } from "./SslToolbar";

const HIGHLIGHT_MS = 1400;

function SkeletonRows() {
  return (
    <div className="skeleton">
      {[0, 1, 2].map((i) => (
        <div className="skeleton__row" key={i} style={{ animationDelay: `${i * 120}ms` }} />
      ))}
    </div>
  );
}

/** Group id → display name. Groups are optional, so a failed load just means "no names". */
function useGroupNames(): Map<number, string> {
  const { groups } = useGroups();
  return useMemo(() => new Map((groups ?? []).map((g) => [g.id, g.name])), [groups]);
}

function openDomain(record: DomainRecord) {
  navigate(`/ssl/${encodeURIComponent(record.domain)}`);
}

/** `selectedDomain` comes from the URL (`#/ssl/<key>`) and opens that domain's detail drawer. */
export function SslPage({ selectedDomain }: { selectedDomain?: string }) {
  const { user } = useAuth();
  const toast = useToast();
  const groupNames = useGroupNames();
  const [domains, setDomains] = useState<DomainRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [confirmBulk, setConfirmBulk] = useState(false);
  const [highlightKey, setHighlightKey] = useState<string | null>(null);
  const [selectedKeys, setSelectedKeys] = useState<Set<string>>(new Set());
  const [filterQuery, setFilterQuery] = useState("");
  const [filterStatus, setFilterStatus] = useState<Status | "all">("all");
  const [showAddDomain, setShowAddDomain] = useState(false);
  const [showBulkImport, setShowBulkImport] = useState(false);

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

  const selected = domains.find((d) => d.domain === selectedDomain) ?? null;

  const filteredDomains = useMemo(() => {
    const q = filterQuery.toLowerCase();
    return domains.filter(
      (d) => (q === "" || d.host.toLowerCase().includes(q)) && (filterStatus === "all" || d.status === filterStatus),
    );
  }, [domains, filterQuery, filterStatus]);

  useEffect(() => { setSelectedKeys(new Set()); }, [filterQuery, filterStatus]);

  function flashRow(key: string) {
    setHighlightKey(key);
    window.setTimeout(() => setHighlightKey(null), HIGHLIGHT_MS);
  }

  function handleToggleSelect(key: string) {
    setSelectedKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function handleSelectAll() {
    const allSelected = selectedKeys.size === filteredDomains.length;
    setSelectedKeys(allSelected ? new Set() : new Set(filteredDomains.map((d) => d.domain)));
  }

  async function handleDeleteSelected() {
    const keys = [...selectedKeys];
    setSelectedKeys(new Set());
    await Promise.allSettled(keys.map((k) => api.deleteDomain(k)));
    await refresh();
    toast.show(`Removed ${plural(keys.length, "domain")}`, "info");
  }

  async function handleAdd(domain: string, port: number, agentId?: string): Promise<boolean> {
    setBusy(true);
    try {
      const created = await api.addDomain(domain, port, agentId);
      await refresh();
      flashRow(created.domain);
      toast.show(`${domain} is now being monitored`, "success");
      return true;
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Could not add domain", "error");
      return false;
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
      <PageHeader
        title="SSL Certificates"
        subtitle="TLS certificate expiry across all monitored hosts"
        actions={
          <SslToolbar
            domains={domains}
            isAdmin={user?.role === "admin"}
            onAdd={() => setShowAddDomain(true)}
            onImport={() => setShowBulkImport(true)}
          />
        }
      />

      <div className="overview">
        <SummaryBar domains={domains} filter={filterStatus} onFilter={setFilterStatus} />
        <HealthRing domains={domains} />
      </div>

      <AnimatePresence>
        {!loading && <ForecastWidget domains={domains} onSelect={openDomain} />}
      </AnimatePresence>

      <SearchFilter
        query={filterQuery}
        status={filterStatus}
        total={domains.length}
        filtered={filteredDomains.length}
        onQuery={setFilterQuery}
        onStatus={setFilterStatus}
      />

      <SslBulkBar
        count={selectedKeys.size}
        onDeselect={() => setSelectedKeys(new Set())}
        onDelete={() => setConfirmBulk(true)}
      />

      {loading ? (
        <SkeletonRows />
      ) : domains.length === 0 ? (
        <SslEmptyState />
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
          groupNames={groupNames}
          selectedKeys={selectedKeys}
          onToggleSelect={handleToggleSelect}
          onSelectAll={handleSelectAll}
          onDelete={setConfirmDelete}
          onSelect={openDomain}
          deletingKey={deletingKey}
          running={false}
          highlightKey={highlightKey}
        />
      )}

      <AnimatePresence>
        {confirmDelete && (
          <ConfirmDialog
            key="confirm-delete"
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
        {confirmBulk && (
          <ConfirmDialog
            key="confirm-bulk"
            title="Stop monitoring?"
            message={<>Remove <strong>{plural(selectedKeys.size, "domain")}</strong> from the dashboard? This can't be undone in one click.</>}
            confirmLabel={`Remove ${selectedKeys.size}`}
            onCancel={() => setConfirmBulk(false)}
            onConfirm={() => {
              setConfirmBulk(false);
              void handleDeleteSelected();
            }}
          />
        )}
        {showAddDomain && <AddDomainModal key="add" onClose={() => setShowAddDomain(false)} onAdd={handleAdd} busy={busy} />}
        {showBulkImport && (
          <BulkImportModal
            key="import"
            onClose={() => setShowBulkImport(false)}
            onImported={(added) => {
              void refresh();
              if (added.length > 0) {
                flashRow(added[added.length - 1].domain);
                toast.show(`${plural(added.length, "domain")} imported`, "success");
              }
            }}
          />
        )}
        {selected && (
          <DomainDetail
            key={selected.domain}
            record={selected}
            onClose={() => navigate("/ssl")}
            onSaved={(updated) => {
              setDomains((prev) => prev.map((d) => (d.domain === updated.domain ? updated : d)));
              void refresh();
            }}
          />
        )}
      </AnimatePresence>
    </>
  );
}
