import type { DomainRecord } from "../../types";
import { navigate } from "../../hooks/useHashRoute";
import { StatusBadge } from "../StatusBadge";

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
}

type Props = {
  domains: DomainRecord[];
  removingKey: string | null;
  onRemove: (domain: DomainRecord) => void;
};

export function GroupDomainTable({ domains, removingKey, onRemove }: Props) {
  return (
    <div className="tablewrap grp-tablescroll">
      <table className="table grp-table">
        <thead>
          <tr>
            <th scope="col">Host</th>
            <th scope="col">Status</th>
            <th scope="col">Days left</th>
            <th scope="col" className="grp-col-expiry">Expires</th>
            <th scope="col"><span className="grp-sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          {domains.map((d) => (
            <tr key={d.domain} className={`row row--${d.status}`}>
              <td>
                <button
                  type="button"
                  className="grp-link domain"
                  onClick={() => navigate(`/ssl/${encodeURIComponent(d.domain)}`)}
                  title="Open this certificate"
                >
                  {d.host}
                  {d.port !== 443 && <span className="domain__port">:{d.port}</span>}
                </button>
              </td>
              <td><StatusBadge status={d.status} /></td>
              <td className={`grp-days grp-days--${d.status}`}>
                {d.days_remaining === null ? "—" : `${d.days_remaining}d`}
              </td>
              <td className="grp-col-expiry">{formatDate(d.not_after)}</td>
              <td className="grp-col-action">
                <button
                  type="button"
                  className="btn btn--ghost btn--icon"
                  onClick={() => onRemove(d)}
                  disabled={removingKey === d.domain}
                  aria-label={`Remove ${d.domain} from group`}
                >
                  {removingKey === d.domain ? "Removing…" : "Remove from group"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
