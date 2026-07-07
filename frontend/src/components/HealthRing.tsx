import { motion } from "motion/react";
import type { DomainRecord, Status } from "../types";
import { useCountUp } from "../hooks/useCountUp";

const R = 42;
const C = 2 * Math.PI * R;
const GAP = 4;

const COLORS: Record<Status, string> = {
  ok:          "#3fb86f",
  warning:     "#e0a23a",
  expired:     "#f0533f",
  unreachable: "#7c8696",
};

const LABELS: Record<Status, string> = {
  ok:          "Healthy",
  warning:     "Warning",
  expired:     "Expired",
  unreachable: "Unreachable",
};

const ORDER: Status[] = ["ok", "warning", "expired", "unreachable"];

interface Seg { status: Status; count: number; length: number; offset: number; }

function buildSegments(domains: DomainRecord[]): Seg[] {
  const total = domains.length;
  if (!total) return [];
  const counts = { ok: 0, warning: 0, expired: 0, unreachable: 0 } as Record<Status, number>;
  domains.forEach((d) => counts[d.status]++);
  let cum = 0;
  return ORDER.filter((s) => counts[s] > 0).map((s) => {
    const alloc = (counts[s] / total) * C;
    const seg: Seg = { status: s, count: counts[s], length: Math.max(0, alloc - GAP), offset: C / 4 - cum };
    cum += alloc;
    return seg;
  });
}

export function HealthRing({ domains }: { domains: DomainRecord[] }) {
  const total = useCountUp(domains.length);
  const counts = { ok: 0, warning: 0, expired: 0, unreachable: 0 } as Record<Status, number>;
  domains.forEach((d) => counts[d.status]++);
  const segs = buildSegments(domains);
  const hasData = domains.length > 0;

  return (
    <div className="hring">
      <div className="hring__chart">
        <svg viewBox="0 0 100 100" width="100" height="100" aria-hidden="true">
          {/* track */}
          <circle cx="50" cy="50" r={R} fill="none" stroke="var(--line)" strokeWidth="9" />
          {/* segments */}
          {hasData && segs.map((seg, i) => (
            <motion.circle
              key={seg.status}
              cx="50" cy="50" r={R}
              fill="none"
              stroke={COLORS[seg.status]}
              strokeWidth="9"
              strokeLinecap="butt"
              strokeDashoffset={seg.offset}
              initial={{ strokeDasharray: `0 ${C}` }}
              animate={{ strokeDasharray: `${seg.length} ${C - seg.length}` }}
              transition={{ duration: 0.85, ease: "easeOut", delay: i * 0.08 }}
            />
          ))}
        </svg>
        <div className="hring__center">
          <span className="hring__num">{total}</span>
          <span className="hring__label">domains</span>
        </div>
      </div>

      <ul className="hring__legend">
        {ORDER.map((s) => (
          <li key={s} className="hring__leg-item" style={{ opacity: counts[s] ? 1 : 0.35 }}>
            <span className="hring__leg-dot" style={{ background: COLORS[s] }} />
            <span className="hring__leg-label">{LABELS[s]}</span>
            <span className="hring__leg-count">{counts[s]}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
