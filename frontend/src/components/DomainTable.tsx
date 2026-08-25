import { AnimatePresence, motion } from "motion/react";
import type { DomainRecord } from "../types";
import { StatusBadge } from "./StatusBadge";

type Props = {
  domains: DomainRecord[];
  selectedKeys: Set<string>;
  onToggleSelect: (key: string) => void;
  onSelectAll: () => void;
  onDelete: (domainKey: string) => void;
  onSelect: (record: DomainRecord) => void;
  deletingKey: string | null;
  running: boolean;
  highlightKey: string | null;
};

function formatExpiry(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
  });
}

function DaysCell({ record }: { record: DomainRecord }) {
  const { days_remaining: days, status } = record;
  if (days === null) {
    return <span className="days days--na">{record.last_error ? "unreachable" : "—"}</span>;
  }
  const pct = Math.max(0, Math.min(100, (days / 90) * 100));
  return (
    <div className="days">
      <span className={`days__num days__num--${status}`}>{days}d</span>
      <span className="days__track">
        <motion.span
          className={`days__fill days__fill--${status}`}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.7, ease: "easeOut" }}
        />
      </span>
    </div>
  );
}

export function DomainTable({
  domains,
  selectedKeys,
  onToggleSelect,
  onSelectAll,
  onDelete,
  onSelect,
  deletingKey,
  running,
  highlightKey,
}: Props) {
  const allSelected = domains.length > 0 && selectedKeys.size === domains.length;
  const someSelected = selectedKeys.size > 0 && !allSelected;

  return (
    <div className={`tablewrap ${running ? "is-scanning" : ""}`}>
      {running && <div className="scan-sweep" aria-hidden="true" />}
      <table className="table">
        <thead>
          <tr>
            <th className="col-check">
              <input
                type="checkbox"
                className="row-check"
                checked={allSelected}
                ref={(el) => { if (el) el.indeterminate = someSelected; }}
                onChange={onSelectAll}
                aria-label="Select all"
              />
            </th>
            <th className="col-domain">Domain</th>
            <th className="col-expiry">Expires</th>
            <th className="col-days">Days left</th>
            <th className="col-status">Status</th>
            <th className="col-actions" aria-label="actions" />
          </tr>
        </thead>
        <tbody>
          <AnimatePresence initial={false}>
            {domains.map((d) => {
              const isSelected = selectedKeys.has(d.domain);
              return (
                <motion.tr
                  key={d.domain}
                  layout
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, x: -28, backgroundColor: "rgba(240,83,63,0.12)" }}
                  transition={{ type: "spring", stiffness: 380, damping: 30 }}
                  whileHover={{ x: 4 }}
                  className={`row row--clickable row--${d.status} ${isSelected ? "row--selected" : ""} ${highlightKey === d.domain ? "row--new" : ""}`}
                  onClick={() => onSelect(d)}
                >
                  <td className="col-check" onClick={(e) => e.stopPropagation()}>
                    <input
                      type="checkbox"
                      className="row-check"
                      checked={isSelected}
                      onChange={() => onToggleSelect(d.domain)}
                      aria-label={`Select ${d.host}`}
                    />
                  </td>
                  <td className="col-domain">
                    <span className="domain">{d.host}</span>
                    {d.port !== 443 && <span className="domain__port">:{d.port}</span>}
                    {d.last_error && (
                      <span className="domain__err" title={d.last_error}>
                        {d.last_error}
                      </span>
                    )}
                  </td>
                  <td className="col-expiry">{formatExpiry(d.not_after)}</td>
                  <td className="col-days">
                    <DaysCell record={d} />
                  </td>
                  <td className="col-status">
                    <StatusBadge status={d.status} />
                  </td>
                  <td className="col-actions">
                    <motion.button
                      className="btn btn--ghost btn--icon"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(d.domain);
                      }}
                      disabled={deletingKey === d.domain}
                      aria-label={`Stop monitoring ${d.domain}`}
                      title="Stop monitoring"
                      whileTap={{ scale: 0.8, rotate: 90 }}
                    >
                      ✕
                    </motion.button>
                  </td>
                </motion.tr>
              );
            })}
          </AnimatePresence>
        </tbody>
      </table>
    </div>
  );
}
