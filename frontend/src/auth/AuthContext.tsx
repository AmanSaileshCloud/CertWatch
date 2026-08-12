import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api, clearToken, getToken, setToken } from "../api";
import type { User } from "../types";

type AuthState = {
  user: User | null;
  ready: boolean; // initial token check finished
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  // On mount: if a token is stored, validate it via /auth/me.
  useEffect(() => {
    let cancelled = false;
    const token = getToken();
    if (!token) {
      setReady(true);
      return;
    }
    api
      .me()
      .then((u) => !cancelled && setUser(u))
      .catch(() => clearToken())
      .finally(() => !cancelled && setReady(true));
    return () => {
      cancelled = true;
    };
  }, []);

  // A 401 anywhere in the app clears the session.
  useEffect(() => {
    const onUnauthorized = () => {
      clearToken();
      setUser(null);
    };
    window.addEventListener("auth:unauthorized", onUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", onUnauthorized);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const res = await api.login(username, password);
    setToken(res.access_token);
    setUser(res.user);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, ready, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
