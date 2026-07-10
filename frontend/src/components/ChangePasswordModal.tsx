import { useState, type FormEvent } from "react";
import { motion } from "motion/react";
import { api, ApiError } from "../api";
import { useToast } from "./Toast";

export function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);
    if (next.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    if (next !== confirm) {
      setError("New passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      await api.changePassword(current, next);
      toast.show("Password changed", "success");
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not change password");
    } finally {
      setBusy(false);
    }
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
          <h2>Change password</h2>
          <button className="banner__close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <form className="login__form" onSubmit={submit}>
          <label className="login__field">
            <span>Current password</span>
            <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoFocus required />
          </label>
          <label className="login__field">
            <span>New password</span>
            <input type="password" value={next} onChange={(e) => setNext(e.target.value)} required />
          </label>
          <label className="login__field">
            <span>Confirm new password</span>
            <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
          </label>
          {error && <div className="login__error" role="alert">{error}</div>}
          <motion.button
            type="submit"
            className="btn btn--primary"
            disabled={busy || !current || !next || !confirm}
            whileTap={{ scale: 0.97 }}
          >
            {busy ? "Changing…" : "Change password"}
          </motion.button>
        </form>
      </motion.div>
    </motion.div>
  );
}
