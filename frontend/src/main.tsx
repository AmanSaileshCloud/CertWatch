import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { AuthProvider } from "./auth/AuthContext.tsx";
import { ToastProvider } from "./components/Toast.tsx";
import App from "./App.tsx";
import "./styles.css";
import "./styles/shell.css";
import "./styles/websites.css";
import "./styles/groups.css";
import "./styles/alerts.css";
import "./styles/settings.css";

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("root element missing");

createRoot(rootEl).render(
  <StrictMode>
    <AuthProvider>
      <ToastProvider>
        <App />
      </ToastProvider>
    </AuthProvider>
  </StrictMode>,
);
