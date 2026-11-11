import { useState, type KeyboardEvent } from "react";
import { isValidEmail } from "../../lib/alerts";

type Props = {
  id: string;
  value: string[];
  disabled?: boolean;
  onChange: (next: string[]) => void;
};

/** Email chips: type an address and press Enter (or "Add"); × removes one. */
export function RecipientChips({ id, value, disabled, onChange }: Props) {
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  function add() {
    const email = input.trim();
    if (!email) return;
    if (!isValidEmail(email)) return setError("Enter an email address like name@example.com.");
    if (value.some((v) => v.toLowerCase() === email.toLowerCase())) return setError("That address is already in the list.");
    onChange([...value, email]);
    setInput("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Enter") return;
    e.preventDefault(); // don't submit the whole section
    add();
  }

  return (
    <div className="settings__chips">
      {value.length === 0 ? (
        <p className="alertrcpt__muted">No default recipients.</p>
      ) : (
        <ul className="settings__chiplist">
          {value.map((email) => (
            <li key={email} className="settings__chip">
              <code className="settings__chiptext">{email}</code>
              <button
                type="button"
                className="alertrcpt__remove"
                onClick={() => onChange(value.filter((v) => v !== email))}
                disabled={disabled}
                aria-label={`Remove ${email}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="alertrcpt__add">
        <input
          id={id}
          type="email"
          className="alertrcpt__input"
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setError(null);
          }}
          onKeyDown={onKeyDown}
          placeholder="name@example.com"
          aria-invalid={error !== null}
          maxLength={254}
          disabled={disabled}
        />
        <button type="button" className="btn btn--ghost btn--icon" onClick={add} disabled={disabled || !input.trim()}>
          Add
        </button>
        {error && <em className="monform__error alertrcpt__error" role="alert">{error}</em>}
      </div>
    </div>
  );
}
