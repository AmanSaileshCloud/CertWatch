import type { ReactNode } from "react";
import { motion } from "motion/react";
import type { GroupHealth } from "../../types";
import { useEscapeKey } from "../../hooks/useEscapeKey";

const HEALTH_LABELS: Record<GroupHealth, string> = {
  ok: "Healthy",
  warning: "Warning",
  down: "Problem",
  empty: "Empty",
};

/** Traffic-light dot plus a text label, so colour is never the only signal. */
export function HealthLight({ health, showLabel = true }: { health: GroupHealth; showLabel?: boolean }) {
  const label = HEALTH_LABELS[health];
  return (
    <span className={`grp-health grp-health--${health}`} title={`Health: ${label}`}>
      <span className="grp-health__dot" aria-hidden="true" />
      {showLabel ? (
        <span className="grp-health__label">{label}</span>
      ) : (
        <span className="grp-sr-only">Health: {label}</span>
      )}
    </span>
  );
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** Shared animated modal frame (same look as AddDomainModal). Escape closes it unless `busy`. */
export function ModalShell({
  title,
  onClose,
  busy = false,
  children,
}: {
  title: string;
  onClose: () => void;
  busy?: boolean;
  children: ReactNode;
}) {
  useEscapeKey(onClose, !busy);
  return (
    <motion.div
      className="modal__backdrop"
      onClick={onClose}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
    >
      <motion.div
        className="modal digest-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 20, opacity: 0 }}
        transition={{ type: "spring", stiffness: 320, damping: 28 }}
      >
        <div className="modal__head">
          <h2>{title}</h2>
          <button type="button" className="banner__close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </motion.div>
    </motion.div>
  );
}

export function InlineError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="grp-error" role="alert">
      {message}
    </p>
  );
}
