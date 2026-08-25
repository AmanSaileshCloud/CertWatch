import { useAuth } from "./auth/AuthContext";
import { useTheme } from "./hooks/useTheme";
import { Login } from "./components/Login";
import { Dashboard } from "./components/Dashboard";

export default function App() {
  const { user, ready } = useAuth();
  const { theme, preference, cycle } = useTheme();

  if (!ready) {
    return (
      <div className="splash">
        <div className="spinner" aria-hidden="true" />
      </div>
    );
  }

  return user ? (
    <Dashboard theme={theme} preference={preference} onToggleTheme={cycle} />
  ) : (
    <Login />
  );
}
