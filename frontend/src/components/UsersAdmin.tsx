import { useEffect, useState, type FormEvent } from "react";
import { motion } from "motion/react";
import { api, ApiError } from "../api";
import type { AdminUser } from "../types";
import { useToast } from "./Toast";

export function UsersAdmin({ onClose, currentUser }: { onClose: () => void; currentUser?: string }) {
  const toast = useToast();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setUsers(await api.listUsers());
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Failed to load users", "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleAdd(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.addUser({ username: username.trim(), password, role });
      toast.show(`User "${username.trim()}" created`, "success");
      setUsername("");
      setPassword("");
      setRole("user");
      await refresh();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Could not create user", "error");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(name: string) {
    try {
      await api.deleteUser(name);
      toast.show(`Removed "${name}"`, "info");
      await refresh();
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : "Could not delete user", "error");
    }
  }

  return (
    <motion.div className="modal__backdrop" onClick={onClose} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
      <motion.div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        initial={{ y: 22, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 22, opacity: 0 }}
        transition={{ type: "spring", stiffness: 320, damping: 28 }}
      >
        <div className="modal__head">
          <h2>Users</h2>
          <button className="banner__close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <form className="userform" onSubmit={handleAdd}>
          <div className="userform__row">
            <label className="addform__field">
              <span className="addform__label">Username</span>
              <input value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />
            </label>
            <label className="addform__field">
              <span className="addform__label">Password</span>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            <label className="addform__field" style={{ maxWidth: 130 }}>
              <span className="addform__label">Role</span>
              <select value={role} onChange={(e) => setRole(e.target.value as "user" | "admin")}>
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </label>
            <button className="btn btn--primary" disabled={busy || !username.trim() || !password}>
              {busy ? "Adding…" : "Add user"}
            </button>
          </div>
        </form>

        {loading ? (
          <p className="users__empty">Loading…</p>
        ) : users.length === 0 ? (
          <p className="users__empty">No users yet.</p>
        ) : (
          <ul className="users__list">
            {users.map((u) => (
              <li key={u.username} className="users__item">
                <span className="users__avatar" aria-hidden="true">{u.username.slice(0, 1).toUpperCase()}</span>
                <span className="users__name">{u.username}</span>
                <span className={`users__role users__role--${u.role}`}>{u.role}</span>
                {u.username === currentUser ? (
                  <span className="users__you">you</span>
                ) : (
                  <button className="btn btn--ghost users__del" onClick={() => handleDelete(u.username)}>
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </motion.div>
    </motion.div>
  );
}
