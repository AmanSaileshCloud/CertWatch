import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useAuth } from "../../auth/AuthContext";
import { useHashRoute, type Route } from "../../hooks/useHashRoute";
import type { ThemePreference } from "../../hooks/useTheme";
import { AuroraBackground } from "../AuroraBackground";
import { ChangePasswordModal } from "../ChangePasswordModal";
import { UsersAdmin } from "../UsersAdmin";
import { SslPage } from "../ssl/SslPage";
import { WebsitesPage } from "../websites/WebsitesPage";
import { MonitorDetailPage } from "../websites/MonitorDetailPage";
import { GroupsPage } from "../groups/GroupsPage";
import { GroupDetailPage } from "../groups/GroupDetailPage";
import { ReportsPage } from "../reports/ReportsPage";
import { SettingsPage } from "../settings/SettingsPage";
import { Sidebar } from "./Sidebar";
import { ThemeCycleButton } from "./ThemeCycleButton";
import logoUrl from "../../assets/WorkmatesLogo.png";

interface AppShellProps {
  theme: "dark" | "light";
  preference: ThemePreference;
  onToggleTheme: () => void;
}

function renderPage(route: Route, isAdmin: boolean) {
  switch (route.page) {
    case "ssl":
      return <SslPage selectedDomain={route.domain} />;
    case "websites":
      return <WebsitesPage />;
    case "monitor":
      return <MonitorDetailPage key={route.id} monitorId={route.id} />;
    case "groups":
      return <GroupsPage />;
    case "group":
      return <GroupDetailPage key={route.id} groupId={route.id} />;
    case "reports":
      return <ReportsPage />;
    case "settings":
      return <SettingsPage isAdmin={isAdmin} />;
  }
}

function Brand() {
  return (
    <div className="brand">
      <motion.div
        className="brand__logo"
        initial={{ opacity: 0, x: -28, scale: 0.85 }}
        animate={{ opacity: 1, x: 0, scale: 1 }}
        transition={{ type: "spring", stiffness: 320, damping: 22, delay: 0.08 }}
      >
        <div className="brand__logo-icon">
          <img src={logoUrl} alt="" aria-hidden="true" />
        </div>
      </motion.div>
      <span className="brand__divider" aria-hidden="true" />
      <div className="brand__text">
        <h1>CERT<span className="brand__accent">WATCH</span></h1>
        <p className="brand__sub">SSL &amp; uptime surveillance · by Workmates</p>
      </div>
    </div>
  );
}

export function AppShell({ theme, preference, onToggleTheme }: AppShellProps) {
  const { user, logout } = useAuth();
  const route = useHashRoute();
  const [showChangePw, setShowChangePw] = useState(false);
  const [showUsers, setShowUsers] = useState(false);
  const isAdmin = user?.role === "admin";

  return (
    <>
      {/* Light theme hides the aurora via CSS — don't pay for rendering it. */}
      {theme === "dark" && <AuroraBackground />}
      <div className="app app--shell">
        <div className="scanline" aria-hidden="true" />
        <header className="header">
          <Brand />
          <div className="header__actions">
            <ThemeCycleButton preference={preference} onCycle={onToggleTheme} />
            {isAdmin && (
              <button className="btn btn--ghost" onClick={() => setShowUsers(true)} title="Manage users">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" style={{ marginRight: 5, verticalAlign: "middle" }}>
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                </svg>
                Users
              </button>
            )}
            <div className="usermenu">
              <span className="usermenu__avatar" aria-hidden="true">
                {(user?.username ?? "?").slice(0, 1).toUpperCase()}
              </span>
              <span className="usermenu__name">{user?.username}</span>
              <button
                className="btn btn--ghost btn--icon"
                onClick={() => setShowChangePw(true)}
                title="Change password"
                aria-label="Change password"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
                </svg>
              </button>
              <button className="btn btn--ghost usermenu__logout" onClick={logout} title="Sign out">
                Sign out
              </button>
            </div>
          </div>
        </header>

        <div className="shell">
          <Sidebar route={route} isAdmin={isAdmin} />
          <main className="main shell__main">{renderPage(route, isAdmin)}</main>
        </div>

        <footer className="footer">
          <span className="footer__brand">© Workmates</span>
          <span>SSL thresholds 30 · 14 · 7 · 1 days</span>
          <span>Local mode · console notifier</span>
        </footer>

        <AnimatePresence>
          {showChangePw && <ChangePasswordModal onClose={() => setShowChangePw(false)} />}
        </AnimatePresence>
        <AnimatePresence>
          {showUsers && <UsersAdmin onClose={() => setShowUsers(false)} currentUser={user?.username} />}
        </AnimatePresence>
      </div>
    </>
  );
}
