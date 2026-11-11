import { useState, type FormEvent } from "react";
import { api } from "../../api";
import type { Customer, GroupSummary } from "../../types";
import { invalidateGroupCache } from "./GroupPicker";
import { InlineError, ModalShell, errorText } from "./groupsUi";

type Props = {
  /** Present = rename this group; absent = create a new one. */
  group?: Pick<GroupSummary, "id" | "name">;
  /** Customers to choose from when creating (ignored when `customerId` is fixed). */
  customers?: Customer[];
  /** Create the group under this customer without showing a picker. */
  customerId?: number;
  onClose: () => void;
  onSaved: (group: GroupSummary) => void;
};

export function GroupFormModal({ group, customers = [], customerId, onClose, onSaved }: Props) {
  const [name, setName] = useState(group?.name ?? "");
  const [chosenCustomer, setChosenCustomer] = useState<number | "">(customerId ?? customers[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isRename = group !== undefined;
  const showCustomerSelect = !isRename && customerId === undefined;
  const fixedCustomerName = customers.find((c) => c.id === customerId)?.name;

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || (!isRename && chosenCustomer === "")) return;
    setBusy(true);
    setError(null);
    try {
      const saved = isRename
        ? await api.renameGroup(group.id, trimmed)
        : await api.addGroup(Number(chosenCustomer), trimmed);
      invalidateGroupCache();
      onSaved(saved);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  const title = isRename ? "Rename group" : fixedCustomerName ? `Add group to ${fixedCustomerName}` : "Add group";

  return (
    <ModalShell busy={busy} title={title} onClose={onClose}>
      <form className="login__form" onSubmit={submit}>
        {showCustomerSelect && (
          <label className="login__field">
            <span>Customer</span>
            <select
              className="grp-select"
              value={chosenCustomer}
              onChange={(e) => setChosenCustomer(Number(e.target.value))}
              required
            >
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="login__field">
          <span>Group name</span>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="E-commerce"
            maxLength={200}
            autoComplete="off"
            autoFocus
            required
          />
        </label>
        <InlineError message={error} />
        <button
          type="submit"
          className="btn btn--primary"
          disabled={busy || !name.trim() || (!isRename && chosenCustomer === "")}
        >
          {busy ? "Saving…" : isRename ? "Save name" : "Add group"}
        </button>
      </form>
    </ModalShell>
  );
}
