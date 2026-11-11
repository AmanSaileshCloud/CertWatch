import { SettingField } from "./settingsUi";

type Props = {
  value: string;
  /** A password is stored on the server (it is never sent back). */
  isSet: boolean;
  replacing: boolean;
  encryptionAvailable: boolean;
  error?: string | null;
  onChange: (value: string) => void;
  onReplace: (replacing: boolean) => void;
  onRemove: () => void;
};

/** Write-only SMTP password: shows "saved" instead of the value, with Replace / Remove. */
export function SmtpPasswordField({ value, isSet, replacing, encryptionAvailable, error, onChange, onReplace, onRemove }: Props) {
  const noKeyNote = "The server has no SETTINGS_ENCRYPTION_KEY, so SMTP passwords can't be stored.";

  if (isSet && !replacing) {
    return (
      <SettingField label="SMTP password" error={error}>
        <div className="settings__secret">
          <strong className="settings__secretvalue">•••••••• saved</strong>
          <button type="button" className="btn btn--ghost btn--icon" onClick={() => onReplace(true)} disabled={!encryptionAvailable}>
            Replace
          </button>
          <button type="button" className="btn btn--danger-ghost btn--icon" onClick={onRemove}>
            Remove
          </button>
        </div>
        {!encryptionAvailable && <small className="monform__hint settings__warn">{noKeyNote}</small>}
      </SettingField>
    );
  }

  return (
    <SettingField
      label={replacing ? "New SMTP password" : "SMTP password"}
      htmlFor="settings-smtp-password"
      error={error}
      hint={encryptionAvailable ? "Stored encrypted. It is never shown again after saving." : undefined}
    >
      <div className="settings__secret">
        <input
          id="settings-smtp-password"
          type="password"
          autoComplete="new-password"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={!encryptionAvailable}
          placeholder={encryptionAvailable ? "SES SMTP password" : "Unavailable"}
          aria-invalid={Boolean(error)}
          maxLength={512}
        />
        {replacing && (
          <button
            type="button"
            className="btn btn--ghost btn--icon"
            onClick={() => {
              onChange("");
              onReplace(false);
            }}
          >
            Cancel
          </button>
        )}
      </div>
      {!encryptionAvailable && <small className="monform__hint settings__warn">{noKeyNote}</small>}
    </SettingField>
  );
}
