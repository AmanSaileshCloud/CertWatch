import { useEffect, useState } from "react";

/** The pages of the app, addressed by the URL hash (e.g. `#/websites/6`, `#/ssl/example.com%3A443`). */
export type Route =
  | { page: "ssl"; domain?: string }
  | { page: "websites" }
  | { page: "monitor"; id: number }
  | { page: "groups" }
  | { page: "group"; id: number }
  | { page: "reports" }
  | { page: "settings" };

const DEFAULT_ROUTE: Route = { page: "ssl" };

function safeDecode(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

export function parseRoute(hash: string): Route {
  const [section, rawId] = hash.replace(/^#\/?/, "").split("/");
  const id = Number(rawId);
  const hasId = rawId !== undefined && Number.isInteger(id) && id > 0;
  switch (section) {
    case "websites":
      return hasId ? { page: "monitor", id } : { page: "websites" };
    case "groups":
      return hasId ? { page: "group", id } : { page: "groups" };
    case "reports":
      return { page: "reports" };
    case "settings":
      return { page: "settings" };
    case "ssl":
      return rawId ? { page: "ssl", domain: safeDecode(rawId) } : DEFAULT_ROUTE;
    default:
      return DEFAULT_ROUTE;
  }
}

/** Go to a page, e.g. `navigate("/websites/6")`. Works with the browser back button. */
export function navigate(path: string): void {
  window.location.hash = path.startsWith("/") ? path : `/${path}`;
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
