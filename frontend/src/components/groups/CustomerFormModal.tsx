import { useState, type FormEvent } from "react";
import { api } from "../../api";
import type { Customer } from "../../types";
import { InlineError, ModalShell, errorText } from "./groupsUi";

type Props = {
  /** Present = edit this customer; absent = create a new one. */
  customer?: Customer;
  onClose: () => void;
  onSaved: (customer: Customer) => void;
};

export function CustomerFormModal({ customer, onClose, onSaved }: Props) {
  const [name, setName] = useState(customer?.name ?? "");
  const [email, setEmail] = useState(customer?.contact_email ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isEdit = customer !== undefined;

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) return;
    const contactEmail = email.trim() || null;
    setBusy(true);
    setError(null);
    try {
      const saved = isEdit
        ? await api.updateCustomer(customer.id, { name: trimmedName, contact_email: contactEmail })
        : await api.addCustomer(trimmedName, contactEmail);
      onSaved(saved);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <ModalShell busy={busy} title={isEdit ? "Edit customer" : "Add customer"} onClose={onClose}>
      <form className="login__form" onSubmit={submit}>
        <label className="login__field">
          <span>Customer name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Acme Corp"
            maxLength={200}
            autoComplete="off"
            autoFocus
            required
          />
        </label>
        <label className="login__field">
          <span>Contact email (optional)</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="ops@acme.com"
            autoComplete="off"
          />
        </label>
        <InlineError message={error} />
        <button type="submit" className="btn btn--primary" disabled={busy || !name.trim()}>
          {busy ? "Saving…" : isEdit ? "Save changes" : "Add customer"}
        </button>
      </form>
    </ModalShell>
  );
}
