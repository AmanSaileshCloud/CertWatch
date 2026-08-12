import { useState, type FormEvent } from "react";
import { motion } from "motion/react";
import { ApiError } from "../api";
import { useAuth } from "../auth/AuthContext";
import { LoginCharacters } from "./LoginCharacters";
import { MorphingText } from "./MorphingText";
import logoUrl from "../assets/WorkmatesLogo.png";

const EyeIcon = ({ off }: { off: boolean }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {off ? (
      <>
        <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 10 8 10 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
        <path d="M6.61 6.61A18.5 18.5 0 0 0 2 12s3 8 10 8a9.12 9.12 0 0 0 5.39-1.61" />
        <line x1="2" y1="2" x2="22" y2="22" />
      </>
    ) : (
      <>
        <path d="M2 12s3-8 10-8 10 8 10 8-3 8-10 8-10-8-10-8Z" />
        <circle cx="12" cy="12" r="3" />
      </>
    )}
  </svg>
);

export function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const focusOn = () => setIsTyping(true);
  const focusOff = () => setIsTyping(false);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
      setBusy(false);
    }
  }

  return (
    <div className="authpage">
      {/* Left brand panel with animated characters */}
      <aside className="authpage__brand">
        <div className="authpage__brandtop">
          <span className="authpage__logo">
            <img src={logoUrl} alt="Workmates" />
          </span>
        </div>

        <div className="authpage__chars">
          <LoginCharacters
            isTyping={isTyping}
            passwordVisible={showPassword}
            passwordLength={password.length}
          />
        </div>

        <div className="authpage__morphwrap">
          <MorphingText words={["MONITOR", "PROTECT", "RENEW"]} />
        </div>

        <p className="authpage__brandfoot">TLS certificate surveillance, watched closely.</p>

        <div className="authpage__glow authpage__glow--1" aria-hidden="true" />
        <div className="authpage__glow authpage__glow--2" aria-hidden="true" />
      </aside>

      {/* Right form panel */}
      <main className="authpage__panel">
        <motion.div
          className="authpage__form"
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 240, damping: 24 }}
        >
          <span className="authpage__mlogo">
            <img src={logoUrl} alt="Workmates" />
          </span>

          <h1 className="authpage__title">CERTWatch</h1>
          <p className="authpage__sub">Welcome back — sign in to monitor your certificates.</p>

          <form className="login__form" onSubmit={handleSubmit}>
            <label className="login__field">
              <span>Username</span>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                onFocus={focusOn}
                onBlur={focusOff}
                autoComplete="username"
                autoFocus
                required
              />
            </label>
            <label className="login__field">
              <span>Password</span>
              <div className="pwwrap">
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onFocus={focusOn}
                  onBlur={focusOff}
                  autoComplete="current-password"
                  required
                />
                <button
                  type="button"
                  className="pwtoggle"
                  onClick={() => setShowPassword((s) => !s)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                >
                  <EyeIcon off={showPassword} />
                </button>
              </div>
            </label>

            <motion.button
              type="submit"
              className="btn btn--primary login__submit"
              disabled={busy || !username.trim() || !password}
              whileTap={{ scale: 0.97 }}
            >
              {busy ? "Please wait…" : "Log in"}
            </motion.button>
          </form>

          {error && (
            <motion.div className="login__error" role="alert" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}>
              {error}
            </motion.div>
          )}
        </motion.div>
      </main>
    </div>
  );
}
