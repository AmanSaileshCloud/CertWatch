import { motion } from "motion/react";

export function SslEmptyState() {
  return (
    <motion.div className="empty" initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}>
      <div className="empty__icon" aria-hidden="true">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          <circle cx="12" cy="11" r="3" />
        </svg>
      </div>
      <p className="empty__title">No domains under watch</p>
      <p className="empty__hint">Add a hostname to start monitoring its TLS certificate.</p>
      <ol className="empty__steps">
        <li>Click <strong>Add domain</strong> in the toolbar above</li>
        <li>Enter the hostname — it appears in the table instantly</li>
        <li>Certificate details are fetched automatically</li>
      </ol>
    </motion.div>
  );
}
