import { AnimatePresence, motion } from "motion/react";
import { plural } from "../../lib/format";
import type { DomainRecord } from "../../types";
import { ExportMenu } from "../ExportMenu";

type Props = {
  domains: DomainRecord[];
  isAdmin: boolean;
  onAdd: () => void;
  onImport: () => void;
};

/** Page-header actions for the SSL page: add, bulk import and export. */
export function SslToolbar({ domains, isAdmin, onAdd, onImport }: Props) {
  return (
    <>
      <button className="btn btn--primary" onClick={onAdd} title="Add a single domain to monitor">
        <span className="btn__plus" aria-hidden="true">+</span>
        Add domain
      </button>
      <button className="btn btn--ghost" onClick={onImport} title="Import multiple domains at once">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ marginRight: 5, verticalAlign: "middle" }}>
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
        </svg>
        Import
      </button>
      <ExportMenu domains={domains} isAdmin={isAdmin} />
    </>
  );
}

type BulkBarProps = { count: number; onDeselect: () => void; onDelete: () => void };

/** Appears while table rows are selected; offers deselect and bulk delete. */
export function SslBulkBar({ count, onDeselect, onDelete }: BulkBarProps) {
  return (
    <AnimatePresence>
      {count > 0 && (
        <motion.div className="bulkbar" initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}>
          <span className="bulkbar__count">{count} selected</span>
          <button className="btn btn--ghost bulkbar__deselect" onClick={onDeselect}>
            Deselect all
          </button>
          <button className="btn bulkbar__delete" onClick={onDelete}>
            Delete {plural(count, "domain")}
          </button>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
