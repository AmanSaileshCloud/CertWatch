import { motion } from "motion/react";
import type { DomainRecord, Status } from "../types";
import { useCountUp } from "../hooks/useCountUp";

const ORDER: Status[] = ["expired", "unreachable", "warning", "ok"];
const LABELS: Record<Status, string> = {
  expired: "Expired",
  unreachable: "Unreachable",
  warning: "Warning",
  ok: "Healthy",
};

function Chip({ status, count }: { status: Status; count: number }) {
  const animated = useCountUp(count);
  const active = count > 0 && status !== "ok";
  return (
    <motion.div
      className={`chip chip--${status} ${active ? "chip--active" : ""}`}
      whileHover={{ y: -3 }}
      transition={{ type: "spring", stiffness: 400, damping: 22 }}
    >
      <span className="chip__count">{animated}</span>
      <span className="chip__label">{LABELS[status]}</span>
    </motion.div>
  );
}

export function SummaryBar({ domains }: { domains: DomainRecord[] }) {
  const counts = domains.reduce<Record<string, number>>((acc, d) => {
    acc[d.status] = (acc[d.status] ?? 0) + 1;
    return acc;
  }, {});
  const total = useCountUp(domains.length);

  return (
    <div className="summary">
      <div className="summary__total">
        <span className="summary__total-num">{total}</span>
        <span className="summary__total-label">monitored</span>
      </div>
      <div className="summary__chips">
        {ORDER.map((s) => (
          <Chip key={s} status={s} count={counts[s] ?? 0} />
        ))}
      </div>
    </div>
  );
}
