import type { Customer, GroupSummary } from "../../types";
import { navigate } from "../../hooks/useHashRoute";
import { plural } from "../../lib/format";
import { HealthLight } from "./groupsUi";

type Props = {
  customer: Customer;
  groups: GroupSummary[];
  onAddGroup: () => void;
  onEdit: () => void;
  onDelete: () => void;
};

/** One customer card with its groups and their health lights. */
export function CustomerSection({ customer, groups, onAddGroup, onEdit, onDelete }: Props) {
  return (
    <article className="grp-card" aria-labelledby={`customer-${customer.id}`}>
      <header className="grp-card__head">
        <div className="grp-card__info">
          <h2 id={`customer-${customer.id}`} className="grp-card__name">{customer.name}</h2>
          <p className="grp-card__meta">
            {customer.contact_email ? (
              <a href={`mailto:${customer.contact_email}`}>{customer.contact_email}</a>
            ) : (
              <span>No contact email</span>
            )}
            <span aria-hidden="true"> · </span>
            <span>{plural(customer.group_count, "group")}</span>
          </p>
        </div>
        <div className="grp-actions">
          <button type="button" className="btn btn--icon" onClick={onAddGroup}>
            + Add group
          </button>
          <button type="button" className="btn btn--ghost btn--icon" onClick={onEdit} aria-label={`Edit ${customer.name}`}>
            Edit
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--icon grp-danger"
            onClick={onDelete}
            aria-label={`Delete ${customer.name}`}
          >
            Delete
          </button>
        </div>
      </header>

      {groups.length === 0 ? (
        <p className="grp-card__none">No groups yet — add one to start organising this customer’s sites.</p>
      ) : (
        <ul className="grp-list">
          {groups.map((g) => (
            <li key={g.id}>
              <GroupRow group={g} />
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

function GroupRow({ group }: { group: GroupSummary }) {
  return (
    <button type="button" className="grp-row" onClick={() => navigate(`/groups/${group.id}`)}>
      <HealthLight health={group.health} />
      <span className="grp-row__name">{group.name}</span>
      <span className="grp-row__counts">
        {group.ssl_count} SSL · {plural(group.website_count, "website")}
      </span>
      <span className={`grp-row__problems ${group.problems > 0 ? "is-bad" : ""}`}>
        {group.problems > 0 ? plural(group.problems, "problem") : "No problems"}
      </span>
      <span className="grp-row__chev" aria-hidden="true">›</span>
    </button>
  );
}
