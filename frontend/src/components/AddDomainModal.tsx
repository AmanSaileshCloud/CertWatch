import { useState, type FormEvent } from "react";
import { motion } from "motion/react";

type Props = {
  onClose: () => void;
  onAdd: (domain: string, port: number) => Promise<boolean>;
  busy: boolean;
};

export function AddDomainModal({ onClose, onAdd, busy }: Props) {
  const [domain, setDomain] = useState("");
  const [port, setPort] = useState("443");

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmed = domain.trim();
    if (!trimmed) return;
    const ok = await onAdd(trimmed, Number(port) || 443);
    if (ok) onClose();
  }

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
        onClick={(e) => e.stopPropagation()}
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 20, opacity: 0 }}
        transition={{ type: "spring", stiffness: 320, damping: 28 }}
      >
        <div className="modal__head">
          <h2>Add domain</h2>
          <button className="banner__close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <form className="login__form" onSubmit={submit}>
          <label className="login__field">
            <span>Hostname</span>
            <input
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="example.com"
              autoComplete="off"
              spellCheck={false}
              autoFocus
              required
            />
          </label>
          <label className="login__field">
            <span>Port</span>
            <input
              value={port}
              onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))}
              inputMode="numeric"
              placeholder="443"
            />
          </label>
          <motion.button
            type="submit"
            className="btn btn--primary"
            disabled={busy || !domain.trim()}
            whileTap={{ scale: 0.97 }}
          >
            <span className="btn__plus" aria-hidden="true">+</span>
            {busy ? "Adding…" : "Add domain"}
          </motion.button>
        </form>
      </motion.div>
    </motion.div>
  );
}
