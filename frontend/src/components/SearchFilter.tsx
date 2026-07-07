import type { Status } from "../types";

type FilterStatus = Status | "all";

const PILLS: { value: FilterStatus; label: string }[] = [
  { value: "all",         label: "All" },
  { value: "ok",          label: "Healthy" },
  { value: "warning",     label: "Warning" },
  { value: "expired",     label: "Expired" },
  { value: "unreachable", label: "Unreachable" },
];

interface Props {
  query: string;
  status: FilterStatus;
  total: number;
  filtered: number;
  onQuery: (q: string) => void;
  onStatus: (s: FilterStatus) => void;
}

export function SearchFilter({ query, status, total, filtered, onQuery, onStatus }: Props) {
  const isFiltering = query !== "" || status !== "all";
  return (
    <div className="searchbar">
      <div className="searchbar__input-wrap">
        <svg className="searchbar__icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <input
          className="searchbar__input"
          type="text"
          placeholder="Filter domains…"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
          aria-label="Filter domains"
        />
        {query && (
          <button className="searchbar__clear" onClick={() => onQuery("")} aria-label="Clear filter">✕</button>
        )}
      </div>
      <div className="searchbar__pills">
        {PILLS.map((p) => (
          <button
            key={p.value}
            className={`searchbar__pill ${p.value !== "all" ? `searchbar__pill--${p.value}` : ""} ${status === p.value ? "is-active" : ""}`}
            onClick={() => onStatus(p.value)}
          >
            {p.label}
          </button>
        ))}
      </div>
      {isFiltering && (
        <span className="searchbar__count">
          {filtered} / {total}
        </span>
      )}
    </div>
  );
}
