import { useCallback, useEffect, useState } from "react";
import { AnimatePresence } from "motion/react";
import { api } from "../../api";
import type { DomainRecord, GroupDetail } from "../../types";
import { navigate } from "../../hooks/useHashRoute";
import { useAutoRefresh } from "../../hooks/useAutoRefresh";
import { plural } from "../../lib/format";
import { ConfirmDialog } from "../ConfirmDialog";
import { PageHeader } from "../layout/PageHeader";
import { useToast } from "../Toast";
import { MonitorTable } from "../websites/MonitorTable";
import { AddMemberPanel } from "./AddMemberPanel";
import { GroupDomainTable } from "./GroupDomainTable";
import { GroupFormModal } from "./GroupFormModal";
import { invalidateGroupCache } from "./GroupPicker";
import { HealthLight, errorText } from "./groupsUi";
import { RemoveWebsitesModal } from "./RemoveWebsitesModal";

type Dialog = "rename" | "delete" | "removeWebsites";

function useGroupDetail(groupId: number) {
  const [group, setGroup] = useState<GroupDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setGroup(await api.getGroup(groupId));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [groupId]);

  useEffect(() => {
    setGroup(null);
    setLoading(true);
    void reload();
  }, [reload]);
  useAutoRefresh(() => void reload());

  return { group, loading, error, reload };
}

export function GroupDetailPage({ groupId }: { groupId: number }) {
  const toast = useToast();
  const { group, loading, error, reload } = useGroupDetail(groupId);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [removingKey, setRemovingKey] = useState<string | null>(null);
  const [removingWebsites, setRemovingWebsites] = useState(false);

  async function removeDomain(domain: DomainRecord) {
    setRemovingKey(domain.domain);
    try {
      await api.updateDomain(domain.domain, { group_id: null });
      toast.show(`${domain.host} removed from the group`, "success");
      await reload();
    } catch (e) {
      toast.show(errorText(e), "error");
    } finally {
      setRemovingKey(null);
    }
  }

  async function removeWebsites(ids: number[]) {
    setRemovingWebsites(true);
    const results = await Promise.allSettled(ids.map((id) => api.updateMonitor(id, { group_id: null })));
    const failed = results.filter((r): r is PromiseRejectedResult => r.status === "rejected");
    const removed = ids.length - failed.length;
    if (removed > 0) toast.show(`Removed ${removed} website${removed === 1 ? "" : "s"} from the group`, "success");
    if (failed.length > 0) toast.show(errorText(failed[0].reason), "error");
    setRemovingWebsites(false);
    setDialog(null);
    await reload();
  }

  async function deleteGroup() {
    setDialog(null);
    try {
      await api.deleteGroup(groupId);
      invalidateGroupCache();
      toast.show(`Deleted group ${group?.name ?? ""}`.trim(), "success");
      navigate("/groups");
    } catch (e) {
      toast.show(errorText(e), "error");
    }
  }

  if (loading) {
    return (
      <section className="grp-page">
        <Breadcrumb />
        <div className="skeleton" aria-label="Loading group">
          <div className="skeleton__row" />
          <div className="skeleton__row" />
        </div>
      </section>
    );
  }

  if (!group) {
    return (
      <section className="grp-page">
        <Breadcrumb />
        <div className="empty">
          <div className="empty__title">Could not open this group</div>
          <p className="empty__hint">{error ?? "It may have been deleted."}</p>
          <div className="grp-actions grp-actions--center">
            <button type="button" className="btn" onClick={() => void reload()}>Try again</button>
            <button type="button" className="btn btn--ghost" onClick={() => navigate("/groups")}>Back to groups</button>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="grp-page" aria-label={`Group ${group.name}`}>
      <Breadcrumb customer={group.customer_name} group={group.name} />

      <PageHeader
        title={group.name}
        subtitle={
          <span className="grp-detail-head__summary">
            <HealthLight health={group.health} />
            <span>{group.ssl_count} SSL · {plural(group.website_count, "website")}</span>
            <span className={group.problems > 0 ? "grp-row__problems is-bad" : "grp-row__problems"}>
              {group.problems > 0 ? plural(group.problems, "problem") : "No problems"}
            </span>
          </span>
        }
        actions={
          <>
            <button type="button" className="btn" onClick={() => setDialog("rename")}>Rename</button>
            <button type="button" className="btn btn--ghost grp-danger" onClick={() => setDialog("delete")}>
              Delete group
            </button>
          </>
        }
      />

      {error && (
        <div className="grp-error grp-error--banner" role="alert">
          <span>Auto-refresh failed: {error}</span>
        </div>
      )}

      <AddMemberPanel groupId={group.id} groupName={group.name} onAdded={() => void reload()} />

      <section className="grp-section" aria-labelledby="ssl-title">
        <div className="grp-section__head">
          <h2 id="ssl-title" className="grp-section__title">SSL certificates</h2>
          <span className="grp-section__count">{group.domains.length}</span>
        </div>
        {group.domains.length === 0 ? (
          <SectionEmpty text="No SSL certificates in this group yet. Add a domain above with “SSL certificate” ticked." />
        ) : (
          <GroupDomainTable domains={group.domains} removingKey={removingKey} onRemove={(d) => void removeDomain(d)} />
        )}
      </section>

      <section className="grp-section" aria-labelledby="web-title">
        <div className="grp-section__head">
          <h2 id="web-title" className="grp-section__title">Websites</h2>
          <span className="grp-section__count">{group.monitors.length}</span>
          {group.monitors.length > 0 && (
            <button
              type="button"
              className="btn btn--ghost btn--icon grp-section__action"
              onClick={() => setDialog("removeWebsites")}
            >
              Remove from group…
            </button>
          )}
        </div>
        {group.monitors.length === 0 ? (
          <SectionEmpty text="No websites in this group yet. Add a domain or URL above with “Website uptime” ticked." />
        ) : (
          <MonitorTable monitors={group.monitors} showGroup={false} onOpen={(id) => navigate(`/websites/${id}`)} />
        )}
      </section>

      <AnimatePresence>
        {dialog === "rename" && (
          <GroupFormModal
            key="rename"
            group={group}
            onClose={() => setDialog(null)}
            onSaved={(g) => {
              setDialog(null);
              toast.show(`Renamed to ${g.name}`, "success");
              void reload();
            }}
          />
        )}
        {dialog === "delete" && (
          <ConfirmDialog
            key="delete"
            title={`Delete ${group.name}?`}
            message="The group is removed. Its SSL certificates and websites are kept and keep being monitored — they just become ungrouped."
            confirmLabel="Delete group"
            onConfirm={() => void deleteGroup()}
            onCancel={() => setDialog(null)}
          />
        )}
        {dialog === "removeWebsites" && (
          <RemoveWebsitesModal
            key="remove-websites"
            monitors={group.monitors}
            busy={removingWebsites}
            onClose={() => setDialog(null)}
            onRemove={(ids) => void removeWebsites(ids)}
          />
        )}
      </AnimatePresence>
    </section>
  );
}

function Breadcrumb({ customer, group }: { customer?: string; group?: string }) {
  return (
    <nav className="grp-crumbs" aria-label="Breadcrumb">
      <ol>
        <li>
          <a href="#/groups">Groups</a>
        </li>
        {customer && <li>{customer}</li>}
        {group && <li aria-current="page">{group}</li>}
      </ol>
    </nav>
  );
}

function SectionEmpty({ text }: { text: string }) {
  return (
    <div className="empty grp-section-empty">
      <p className="empty__hint">{text}</p>
    </div>
  );
}
