import { PageHeader } from "../layout/PageHeader";

export function ReportsPage() {
  return (
    <>
      <PageHeader title="Reports" subtitle="Uptime and certificate reports per customer" />
      <div className="empty">
        <div className="empty__icon" aria-hidden="true">
          <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="8" y1="13" x2="16" y2="13" />
            <line x1="8" y1="17" x2="13" y2="17" />
          </svg>
        </div>
        <p className="empty__title">Uptime reports arrive in the next step</p>
        <p className="empty__hint">Monthly uptime and SSL summaries per customer will appear here.</p>
      </div>
    </>
  );
}
