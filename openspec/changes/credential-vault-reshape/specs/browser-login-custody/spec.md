# browser-login-custody (delta)

## MODIFIED Requirements

### Requirement: Separate browser encryption keys from data backups
Browser session state SHALL be sealed under its command center's data key in
the credential vault's one key hierarchy (credential-vault "One key hierarchy,
held only by the broker"), not under a separate raw per-owner key. Data backups
SHALL carry only sealed state and wrapped keys, so a restore with access to the
KMS root keeps owners signed in. Encryption
protects data-only theft, not compromise of the running broker. The raw
per-owner key files on `tinyassets-browser-keys` and any legacy
`.broker/browser-vault` keys SHALL be migrated into the hierarchy once and then
destroyed. Older backups containing raw keys retain their original exposure.

#### Scenario: Migrate or erase an existing owner key
- **WHEN** custody encounters a raw per-owner browser key
- **THEN** it reseals that owner's browser state under the command center's
  data key, verifies it opens, and only then destroys the raw key
- **AND** account erasure destroys the account's wrapped key, and restore
  refuses to re-import it, without touching other owners

#### Scenario: Restore keeps sessions
- **WHEN** a DR restore runs with data backups and KMS access to the root
- **THEN** a remembered browser session opens and needs no new sign-in

#### Scenario: Small storage values occur in page text
- **WHEN** storage contains short values such as `1` or `en`, common tokens such as `undefined`, and a long session token
- **THEN** agent observations preserve ordinary text and redact the session token while retaining the untrusted flag
