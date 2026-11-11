import { useEffect, useState } from "react";
import { api } from "../api";
import type { AlertSettings } from "../types";

/** Global alert defaults; stays null if the API doesn't support alerts yet. */
export function useAlertSettings(): AlertSettings | null {
  const [settings, setSettings] = useState<AlertSettings | null>(null);
  useEffect(() => {
    let active = true;
    api
      .getAlertSettings()
      .then((s) => active && setSettings(s))
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  return settings;
}
