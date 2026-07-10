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

type Filter = Status | "all";

function Chip({
  status,
  count,
  selected,
  onSelect,
}: {
  status: Status;
  count: number;
  selected: boolean;
  onSelect: (f: Filter) => void;
}) {
  const animated = useCountUp(count);
  const hasIssues = count > 0 && status !== "ok";
  return (
    <motion.button
      type="button"
      className={`chip chip--${status} ${hasIssues ? "chip--active" : ""} ${selected ? "chip--selected" : ""}`}
      whileHover={{ y: -3 }}
      whileTap={{ scale: 0.97 }}
      transition={{ type: "spring", stiffness: 400, damping: 22 }}
      onClick={() => onSelect(selected ? "all" : status)}
      title={`Show ${LABELS[status].toLowerCase()} only`}
      aria-pressed={selected}
    >
      <span className="chip__count">{animated}</span>
      <span className="chip__label">{LABELS[status]}</span>
    </motion.button>
  );
}

export function SummaryBar({
  domains,
  filter,
  onFilter,
}: {
  domains: DomainRecord[];
  filter: Filter;
  onFilter: (f: Filter) => void;
}) {
  const counts = domains.reduce<Record<string, number>>((acc, d) => {
    acc[d.status] = (acc[d.status] ?? 0) + 1;
    return acc;
  }, {});
  const total = useCountUp(domains.length);

  return (
    <div className="summary">
      <motion.button
        type="button"
        className={`summary__total ${filter === "all" ? "chip--selected" : ""}`}
        whileHover={{ y: -3 }}
        whileTap={{ scale: 0.97 }}
        transition={{ type: "spring", stiffness: 400, damping: 22 }}
        onClick={() => onFilter("all")}
        title="Show all"
        aria-pressed={filter === "all"}
      >
        <span className="summary__total-num">{total}</span>
        <span className="summary__total-label">monitored</span>
      </motion.button>
      <div className="summary__chips">
        {ORDER.map((s) => (
          <Chip key={s} status={s} count={counts[s] ?? 0} selected={filter === s} onSelect={onFilter} />
        ))}
      </div>
    </div>
  );
}
