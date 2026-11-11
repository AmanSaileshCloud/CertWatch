import { useEffect, useState } from "react";
import { api } from "../../api";
import type { GroupSummary } from "../../types";
import { errorText } from "./groupsUi";

// One shared request for every picker on screen; invalidated after group changes.
let groupsPromise: Promise<GroupSummary[]> | null = null;
const listeners = new Set<() => void>();

function loadGroups(): Promise<GroupSummary[]> {
  if (!groupsPromise) {
    groupsPromise = api.listGroups().catch((e) => {
      groupsPromise = null;
      throw e;
    });
  }
  return groupsPromise;
}

/** Call after creating, renaming or deleting groups so pickers show fresh names. */
export function invalidateGroupCache(): void {
  groupsPromise = null;
  listeners.forEach((notify) => notify());
}

function groupByCustomer(groups: GroupSummary[]): Array<[string, GroupSummary[]]> {
  const byCustomer = new Map<string, GroupSummary[]>();
  for (const g of groups) {
    const list = byCustomer.get(g.customer_name) ?? [];
    list.push(g);
    byCustomer.set(g.customer_name, list);
  }
  return [...byCustomer.entries()];
}

/** Groups from the shared cache; re-fetches whenever any screen invalidates it. */
export function useGroups(): { groups: GroupSummary[] | null; error: string | null; reload: () => void } {
  const [groups, setGroups] = useState<GroupSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const bump = () => setVersion((v) => v + 1);
    listeners.add(bump);
    return () => {
      listeners.delete(bump);
    };
  }, []);

  useEffect(() => {
    let active = true;
    loadGroups()
      .then((list) => {
        if (!active) return;
        setGroups(list);
        setError(null);
      })
      .catch((e) => active && setError(errorText(e)));
    return () => {
      active = false;
    };
  }, [version]);

  return { groups, error, reload: invalidateGroupCache };
}

export function GroupPicker({
  value,
  onChange,
  disabled,
  id,
}: {
  value: number | null;
  onChange: (groupId: number | null) => void;
  disabled?: boolean;
  id?: string;
}) {
  const { groups, error } = useGroups();

  return (
    <select
      id={id}
      className="grp-select"
      value={value ?? ""}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
    >
      <option value="">{error ? "— No group (could not load groups) —" : "— No group —"}</option>
      {groupByCustomer(groups ?? []).map(([customer, list]) => (
        <optgroup key={customer} label={customer}>
          {list.map((g) => (
            <option key={g.id} value={g.id}>
              {customer} · {g.name}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}
