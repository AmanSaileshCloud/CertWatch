import { motion } from "motion/react";
import type { DomainRecord } from "../types";

export function ForecastWidget({ domains, onSelect }: { domains: DomainRecord[]; onSelect: (d: DomainRecord) => void }) {
  const expiring = domains
    .filter((d) => d.days_remaining !== null && d.days_remaining >= 0 && d.days_remaining <= 7)
    .sort((a, b) => (a.days_remaining ?? 0) - (b.days_remaining ?? 0));

  if (!expiring.length) return null;

  return (
    <motion.div
      className="forecast"
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
    >
      <span className="forecast__icon" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
      </span>
      <div className="forecast__body">
        <span className="forecast__title">
          {expiring.length} certificate{expiring.length !== 1 ? "s" : ""} expire within 7 days
        </span>
        <ul className="forecast__list">
          {expiring.map((d) => (
            <li key={d.domain}>
              <button className="forecast__link" onClick={() => onSelect(d)}>
                {d.host}
              </button>
              <span className={`forecast__days ${d.days_remaining === 0 ? "forecast__days--today" : ""}`}>
                {d.days_remaining === 0 ? "today" : `${d.days_remaining}d`}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </motion.div>
  );
}
