import type { MonitorStatus } from "../../types";

/** Reuses the SSL badge colours: up = ok (green), down = expired (red), paused = grey. */
const BADGE_CLASS: Record<MonitorStatus, string> = {
  up: "badge--ok",
  down: "badge--expired",
  paused: "badge--unreachable",
  unknown: "badge--unknown",
};

const LABELS: Record<MonitorStatus, string> = {
  up: "UP",
  down: "DOWN",
  paused: "PAUSED",
  unknown: "UNKNOWN",
};

export function MonitorStatusBadge({ status }: { status: MonitorStatus }) {
  return (
    <span className={`badge ${BADGE_CLASS[status]}`}>
      <span className="badge__dot" aria-hidden="true" />
      {LABELS[status]}
    </span>
  );
}
