import type { ReactNode } from "react";

/** Page title row with optional right-aligned toolbar (actions). */
export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="pagehead">
      <div className="pagehead__text">
        <h2 className="pagehead__title">{title}</h2>
        {subtitle && <p className="pagehead__sub">{subtitle}</p>}
      </div>
      {actions && <div className="pagehead__actions">{actions}</div>}
    </div>
  );
}
