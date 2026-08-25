import { useRef, useState } from "react";
import { motion } from "motion/react";
import { api, ApiError } from "../api";
import type { DomainRecord } from "../types";

type ParsedEntry = { domain: string; port: number };
type ResultEntry = { domain: string; port: number; ok: boolean; error?: string };

interface Props {
  onClose: () => void;
  onImported: (added: DomainRecord[]) => void;
}

function parseLines(text: string): ParsedEntry[] {
  return text
    .split(/[\n,]+/)
    .map((s) => s.trim().replace(/^https?:\/\//, "").replace(/\/$/, ""))
    .filter(Boolean)
    .map((s) => {
      const colonIdx = s.lastIndexOf(":");
      if (colonIdx > 0 && colonIdx < s.length - 1) {
        const maybePort = Number(s.slice(colonIdx + 1));
        if (Number.isInteger(maybePort) && maybePort >= 1 && maybePort <= 65535) {
          return { domain: s.slice(0, colonIdx), port: maybePort };
        }
      }
      return { domain: s, port: 443 };
    })
    .filter((e, i, arr) => arr.findIndex((x) => x.domain === e.domain && x.port === e.port) === i);
}

export function BulkImportModal({ onClose, onImported }: Props) {
  const [text, setText] = useState("");
  const [results, setResults] = useState<ResultEntry[] | null>(null);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const parsed = parseLines(text);

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setText((prev) => (prev ? prev + "\n" : "") + (ev.target?.result as string ?? ""));
    };
    reader.readAsText(file);
    e.target.value = "";
  }

  async function doImport() {
    if (parsed.length === 0) return;
    setImporting(true);
    setResults(null);
    try {
      const res = await api.bulkAddDomains(parsed);
      const resultList: ResultEntry[] = [
        ...res.added.map((d) => ({ domain: d.host, port: d.port, ok: true })),
        ...res.failed.map((f) => ({ domain: f.domain, port: 443, ok: false, error: f.error })),
      ];
      setResults(resultList);
      if (res.added.length > 0) onImported(res.added);
    } catch (err) {
      setResults([{ domain: "–", port: 0, ok: false, error: err instanceof ApiError ? err.message : "Import failed" }]);
    } finally {
      setImporting(false);
    }
  }

  const addedCount = results?.filter((r) => r.ok).length ?? 0;
  const failedCount = results?.filter((r) => !r.ok).length ?? 0;

  return (
    <motion.div
      className="modal__backdrop"
      onClick={onClose}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <motion.div
        className="modal bulk-modal"
        onClick={(e) => e.stopPropagation()}
        initial={{ y: 24, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 24, opacity: 0 }}
        transition={{ type: "spring", stiffness: 320, damping: 28 }}
      >
        <div className="modal__head">
          <h2>Bulk import domains</h2>
          <button className="banner__close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <p className="bulk-modal__hint">
          Paste domains below — one per line. Supports <code>domain:port</code> format and CSV files.
        </p>

        <textarea
          className="bulk-modal__textarea"
          value={text}
          onChange={(e) => { setText(e.target.value); setResults(null); }}
          placeholder={"example.com\napi.mysite.com:8443\nstaging.company.io"}
          rows={8}
          disabled={importing}
          spellCheck={false}
        />

        <div className="bulk-modal__row">
          <button
            className="btn btn--ghost bulk-modal__file-btn"
            onClick={() => fileRef.current?.click()}
            disabled={importing}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
            Upload CSV
          </button>
          <input ref={fileRef} type="file" accept=".csv,.txt" style={{ display: "none" }} onChange={onFileChange} />

          {parsed.length > 0 && !results && (
            <span className="bulk-modal__preview-count">{parsed.length} domain{parsed.length !== 1 ? "s" : ""} ready</span>
          )}

          <motion.button
            className="btn btn--primary bulk-modal__import-btn"
            onClick={doImport}
            disabled={parsed.length === 0 || importing || results !== null}
            whileTap={{ scale: 0.97 }}
          >
            {importing ? "Importing…" : `Import ${parsed.length > 0 ? parsed.length : ""}`}
          </motion.button>
        </div>

        {parsed.length > 0 && !results && (
          <ul className="bulk-modal__preview">
            {parsed.map((e) => (
              <li key={`${e.domain}:${e.port}`} className="bulk-modal__preview-item">
                <span className="bulk-modal__domain">{e.domain}</span>
                {e.port !== 443 && <span className="bulk-modal__port">:{e.port}</span>}
              </li>
            ))}
          </ul>
        )}

        {results && (
          <div className="bulk-modal__results">
            <div className="bulk-modal__results-summary">
              {addedCount > 0 && <span className="bulk-modal__badge bulk-modal__badge--ok">{addedCount} added</span>}
              {failedCount > 0 && <span className="bulk-modal__badge bulk-modal__badge--err">{failedCount} failed</span>}
            </div>
            <ul className="bulk-modal__preview">
              {results.map((r, i) => (
                <li key={i} className={`bulk-modal__preview-item ${r.ok ? "bulk-modal__preview-item--ok" : "bulk-modal__preview-item--err"}`}>
                  <span className="bulk-modal__domain">{r.domain}</span>
                  {r.ok
                    ? <span className="bulk-modal__result-icon">✓</span>
                    : <span className="bulk-modal__result-err">{r.error}</span>
                  }
                </li>
              ))}
            </ul>
            {failedCount === 0 && (
              <button className="btn btn--primary bulk-modal__done-btn" onClick={onClose}>Done</button>
            )}
          </div>
        )}
      </motion.div>
    </motion.div>
  );
}
