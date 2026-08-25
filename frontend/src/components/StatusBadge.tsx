import type { Status } from "../types";

const LABELS: Record<Status, string> = {
  ok: "OK",
  warning: "WARNING",
  expired: "EXPIRED",
  unreachable: "UNREACHABLE",
};

export function StatusBadge({ status }: { status: Status }) {
  return (
    <span className={`badge badge--${status}`}>
      <span className="badge__dot" aria-hidden="true" />
      {LABELS[status]}
    </span>
  );
}
