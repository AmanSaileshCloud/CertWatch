import type { ReactNode } from "react";
import type { Route } from "../../hooks/useHashRoute";

type Section = "ssl" | "websites" | "groups" | "reports";

interface NavItem {
  section: Section;
  label: string;
  href: string;
  icon: ReactNode;
}

const ICON_PROPS = {
  width: 16,
  height: 16,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

const NAV_ITEMS: NavItem[] = [
  {
    section: "ssl",
    label: "SSL Certificates",
    href: "#/ssl",
    icon: (
      <svg {...ICON_PROPS}>
        <rect x="3" y="11" width="18" height="11" rx="2" />
        <path d="M7 11V7a5 5 0 0 1 10 0v4" />
      </svg>
    ),
  },
  {
    section: "websites",
    label: "Websites",
    href: "#/websites",
    icon: (
      <svg {...ICON_PROPS}>
        <circle cx="12" cy="12" r="10" />
        <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
      </svg>
    ),
  },
  {
    section: "groups",
    label: "Groups",
    href: "#/groups",
    icon: (
      <svg {...ICON_PROPS}>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </svg>
    ),
  },
  {
    section: "reports",
    label: "Reports",
    href: "#/reports",
    icon: (
      <svg {...ICON_PROPS}>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="8" y1="13" x2="16" y2="13" />
        <line x1="8" y1="17" x2="13" y2="17" />
      </svg>
    ),
  },
];

/** Detail pages highlight their parent section (a monitor lives under Websites). */
function sectionOf(route: Route): Section {
  if (route.page === "monitor") return "websites";
  if (route.page === "group") return "groups";
  return route.page;
}

export function Sidebar({ route }: { route: Route }) {
  const active = sectionOf(route);
  return (
    <nav className="sidenav" aria-label="Main navigation">
      <ul className="sidenav__list">
        {NAV_ITEMS.map((item) => {
          const isActive = item.section === active;
          return (
            <li key={item.section}>
              <a
                href={item.href}
                className={`sidenav__link ${isActive ? "is-active" : ""}`}
                aria-current={isActive ? "page" : undefined}
              >
                <span className="sidenav__icon">{item.icon}</span>
                <span className="sidenav__label">{item.label}</span>
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
