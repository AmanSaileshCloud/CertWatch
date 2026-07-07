import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "motion/react";
import type { DomainRecord } from "../types";

function download(content: string, filename: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function exportCSV(domains: DomainRecord[]) {
  const headers = ["Domain", "Port", "Status", "Expires", "Days Remaining", "Last Checked", "Alerts Enabled"];
  const rows = domains.map((d) => [
    d.host,
    String(d.port),
    d.status,
    d.not_after ?? "",
    String(d.days_remaining ?? ""),
    d.last_checked_at ?? "",
    String(d.alerts_enabled),
  ]);
  const csv = [headers, ...rows]
    .map((r) => r.map((v) => (v.includes(",") ? `"${v}"` : v)).join(","))
    .join("\n");
  download(csv, `certwatch-export-${new Date().toISOString().slice(0, 10)}.csv`, "text/csv");
}

function exportJSON(domains: DomainRecord[]) {
  download(
    JSON.stringify(domains, null, 2),
    `certwatch-export-${new Date().toISOString().slice(0, 10)}.json`,
    "application/json",
  );
}

export function ExportMenu({ domains }: { domains: DomainRecord[] }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function close(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  return (
    <div className="export-menu" ref={ref}>
      <button
        className="btn btn--ghost export-menu__trigger"
        onClick={() => setOpen((o) => !o)}
        disabled={domains.length === 0}
        title="Export domain list"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" y1="15" x2="12" y2="3" />
        </svg>
        Export
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            className="export-menu__dropdown"
            initial={{ opacity: 0, y: -6, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.97 }}
            transition={{ duration: 0.14 }}
          >
            <button
              className="export-menu__item"
              onClick={() => { exportCSV(domains); setOpen(false); }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" />
              </svg>
              Export as CSV
            </button>
            <button
              className="export-menu__item"
              onClick={() => { exportJSON(domains); setOpen(false); }}
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
              </svg>
              Export as JSON
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
