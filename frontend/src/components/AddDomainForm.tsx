import { useState, type FormEvent } from "react";

type Props = {
  onAdd: (domain: string, port: number) => Promise<void>;
  busy: boolean;
};

export function AddDomainForm({ onAdd, busy }: Props) {
  const [domain, setDomain] = useState("");
  const [port, setPort] = useState("443");

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const trimmed = domain.trim();
    if (!trimmed) return;
    await onAdd(trimmed, Number(port) || 443);
    setDomain("");
    setPort("443");
  }

  return (
    <form className="addform" onSubmit={handleSubmit}>
      <div className="addform__field addform__field--host">
        <label htmlFor="domain">Hostname</label>
        <input
          id="domain"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="example.com"
          autoComplete="off"
          spellCheck={false}
        />
      </div>
      <div className="addform__field addform__field--port">
        <label htmlFor="port">Port</label>
        <input
          id="port"
          value={port}
          onChange={(e) => setPort(e.target.value.replace(/\D/g, ""))}
          inputMode="numeric"
          placeholder="443"
        />
      </div>
      <button type="submit" className="btn btn--primary" disabled={busy || !domain.trim()}>
        <span className="btn__plus" aria-hidden="true">
          +
        </span>
        Monitor
      </button>
    </form>
  );
}
