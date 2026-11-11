import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence } from "motion/react";
import { api } from "../../api";
import type { Customer, GroupSummary } from "../../types";
import { useAutoRefresh } from "../../hooks/useAutoRefresh";
import { plural } from "../../lib/format";
import { ConfirmDialog } from "../ConfirmDialog";
import { PageHeader } from "../layout/PageHeader";
import { useToast } from "../Toast";
import { CustomerFormModal } from "./CustomerFormModal";
import { CustomerSection } from "./CustomerSection";
import { GroupFormModal } from "./GroupFormModal";
import { invalidateGroupCache } from "./GroupPicker";
import { errorText } from "./groupsUi";

type Dialog =
  | { kind: "addCustomer" }
  | { kind: "editCustomer"; customer: Customer }
  | { kind: "deleteCustomer"; customer: Customer }
  | { kind: "addGroup"; customerId?: number };

type Section = { customer: Customer; groups: GroupSummary[] };

function buildSections(customers: Customer[], groups: GroupSummary[], search: string): Section[] {
  const needle = search.trim().toLowerCase();
  const sections: Section[] = [];
  for (const customer of customers) {
    const own = groups.filter((g) => g.customer_id === customer.id);
    const customerMatches = !needle || customer.name.toLowerCase().includes(needle);
    const matchingGroups = customerMatches ? own : own.filter((g) => g.name.toLowerCase().includes(needle));
    if (customerMatches || matchingGroups.length > 0) sections.push({ customer, groups: matchingGroups });
  }
  return sections;
}

export function GroupsPage() {
  const toast = useToast();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [groups, setGroups] = useState<GroupSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [dialog, setDialog] = useState<Dialog | null>(null);

  const load = useCallback(async () => {
    try {
      const [customerList, groupList] = await Promise.all([api.listCustomers(), api.listGroups()]);
      setCustomers(customerList);
      setGroups(groupList);
      setLoadError(null);
    } catch (e) {
      setLoadError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);
  useAutoRefresh(() => void load());

  const sections = useMemo(() => buildSections(customers, groups, search), [customers, groups, search]);
  const problemGroups = groups.filter((g) => g.problems > 0).length;

  function afterChange(message: string) {
    setDialog(null);
    invalidateGroupCache();
    toast.show(message, "success");
    void load();
  }

  async function deleteCustomer(customer: Customer) {
    setDialog(null);
    try {
      await api.deleteCustomer(customer.id);
      afterChange(`Deleted customer ${customer.name}`);
    } catch (e) {
      toast.show(errorText(e), "error");
    }
  }

  return (
    <section className="grp-page" aria-label="Customers and groups">
      <PageHeader
        title="Customers & groups"
        subtitle="Organise SSL certificates and websites by customer and project."
        actions={
          customers.length > 0 && (
            <>
              <button type="button" className="btn" onClick={() => setDialog({ kind: "addGroup" })}>
                + Add group
              </button>
              <button type="button" className="btn btn--primary" onClick={() => setDialog({ kind: "addCustomer" })}>
                <span className="btn__plus" aria-hidden="true">+</span>
                Add customer
              </button>
            </>
          )
        }
      />

      {loadError && (
        <div className="grp-error grp-error--banner" role="alert">
          <span>Could not load customers and groups: {loadError}</span>
          <button type="button" className="btn btn--ghost btn--icon" onClick={() => void load()}>
            Retry
          </button>
        </div>
      )}

      {loading ? (
        <div className="skeleton" aria-label="Loading groups">
          <div className="skeleton__row" />
          <div className="skeleton__row" />
          <div className="skeleton__row" />
        </div>
      ) : customers.length === 0 ? (
        !loadError && <EmptyCustomers onAdd={() => setDialog({ kind: "addCustomer" })} />
      ) : (
        <>
          <div className="grp-toolbar">
            <div className="searchbar__input-wrap">
              <input
                type="search"
                className="searchbar__input grp-search"
                placeholder="Search customers or groups…"
                aria-label="Search customers or groups"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <dl className="grp-stats">
              <div><dt>Customers</dt><dd>{customers.length}</dd></div>
              <div><dt>Groups</dt><dd>{groups.length}</dd></div>
              <div className={problemGroups > 0 ? "grp-stats__bad" : ""}>
                <dt>With problems</dt><dd>{problemGroups}</dd>
              </div>
            </dl>
          </div>

          {sections.length === 0 ? (
            <div className="empty">
              <div className="empty__title">No matches</div>
              <p className="empty__hint">Nothing matches “{search.trim()}”.</p>
            </div>
          ) : (
            <div className="grp-customers">
              {sections.map(({ customer, groups: customerGroups }) => (
                <CustomerSection
                  key={customer.id}
                  customer={customer}
                  groups={customerGroups}
                  onAddGroup={() => setDialog({ kind: "addGroup", customerId: customer.id })}
                  onEdit={() => setDialog({ kind: "editCustomer", customer })}
                  onDelete={() => setDialog({ kind: "deleteCustomer", customer })}
                />
              ))}
            </div>
          )}
        </>
      )}

      <AnimatePresence>
        {dialog?.kind === "addCustomer" && (
          <CustomerFormModal
            key="add-customer"
            onClose={() => setDialog(null)}
            onSaved={(c) => afterChange(`Added customer ${c.name}`)}
          />
        )}
        {dialog?.kind === "editCustomer" && (
          <CustomerFormModal
            key="edit-customer"
            customer={dialog.customer}
            onClose={() => setDialog(null)}
            onSaved={(c) => afterChange(`Saved ${c.name}`)}
          />
        )}
        {dialog?.kind === "addGroup" && (
          <GroupFormModal
            key="add-group"
            customers={customers}
            customerId={dialog.customerId}
            onClose={() => setDialog(null)}
            onSaved={(g) => afterChange(`Added group ${g.name}`)}
          />
        )}
        {dialog?.kind === "deleteCustomer" && (
          <ConfirmDialog
            key="delete-customer"
            title={`Delete ${dialog.customer.name}?`}
            message={
              <>
                Its {plural(dialog.customer.group_count, "group")} will be removed. Domains and websites are
                kept — they just become ungrouped.
              </>
            }
            confirmLabel="Delete customer"
            onConfirm={() => void deleteCustomer(dialog.customer)}
            onCancel={() => setDialog(null)}
          />
        )}
      </AnimatePresence>
    </section>
  );
}

function EmptyCustomers({ onAdd }: { onAdd: () => void }) {
  return (
    <div className="empty grp-empty">
      <div className="empty__icon" aria-hidden="true">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
        </svg>
      </div>
      <div className="empty__title">No customers yet</div>
      <p className="empty__hint grp-empty__text">
        Customers hold groups (for example “Acme Corp → E-commerce”). Put a customer’s SSL certificates and
        websites into a group to see their health at a glance.
      </p>
      <button type="button" className="btn btn--primary" onClick={onAdd}>
        <span className="btn__plus" aria-hidden="true">+</span>
        Add your first customer
      </button>
    </div>
  );
}
