## RENAMED Requirements

- FROM: `As-Built Storage Protection Is Filesystem Permissions Only`
- TO: `Vault File Is Envelope-Encrypted And Filesystem Permissions Still Apply`

## MODIFIED Requirements

### Requirement: Vault File Is Envelope-Encrypted And Filesystem Permissions Still Apply

The vault file `.credential-vault.json` SHALL be an authenticated envelope, not the credential document. Version 1 of that envelope SHALL contain a random 32-byte data key wrapped by AES-GCM under a master key, and the credential document encrypted by AES-GCM under that data key, using the `cryptography` library. The on-disk file SHALL NOT contain a credential secret as recoverable cleartext or as base64. Base64 fields inside the decrypted document (`token_b64`, `secret_b64`, `auth_json_b64`) remain an encoding.

The master key SHALL come from a pluggable provider. The default provider SHALL read `TINYASSETS_VAULT_MASTER_KEY` (32 bytes as base64url or 64-character hex), `TINYASSETS_VAULT_MASTER_KEY_ID` (default `1` when unset), and `TINYASSETS_VAULT_PREVIOUS_MASTER_KEYS` (comma-separated `key_id=material` entries for retired keys). A missing, invalid, or unknown key SHALL fail closed: the vault file is left unchanged, no plaintext vault is written, and no credential records are returned. There SHALL be no ephemeral key and no plaintext fallback.

A legacy unencrypted vault (a JSON list, or a JSON object that has `schema_version` or `credentials` and is not an envelope) SHALL be migrated on load when a master key is configured. Migration SHALL seal the loaded records, decrypt that seal, and replace the file only when the decrypted bytes match the bytes that were sealed. If verification fails, or the file bytes change before replacement, the original file SHALL remain. Migration SHALL be idempotent: loading an envelope SHALL NOT rewrite it except when a caller reseals under the active key. The document SHALL be bound to the command-center directory name so a file copied into a different command center does not authenticate.

Key rotation SHALL store `key_id` on the envelope. A retired key still available to the provider SHALL open that envelope. The next write, and `rewrap_credential_vault`, SHALL seal under the active key.

In-memory records returned by `load_credential_vault`, and the non-secret summary returned by `write_credential_vault`, SHALL keep their existing shapes. The broker read and daemon write file modes below SHALL still apply to the envelope file. Parse and authentication errors SHALL NOT include the file body, a secret, or key material.

Materialized credential artifacts (for example a Codex `auth.json` or a Claude config directory) and per-launch snapshots are not part of this envelope. Their at-rest protection SHALL be POSIX ownership, mode and ACL:

Where every role shares one uid, which is a developer host or a desktop install:
- the vault file and secret files SHALL be `0o600`;
- the `.credentials` artifact directory SHALL be `0o700`.

In the production container (`runtime-process-roles`):
- the daemon uid SHALL be the only writer;
- the vault file and secret files SHALL be `0o640`, with their directories `2750`;
- they SHALL be group-owned by the vault group, whose members are the daemon and broker uids,
  so the broker can read and never write;
- that group SHALL be set on the temporary file before the atomic replace, not inherited from
  the command-center root, and a failure to set it SHALL propagate.

Per-launch credential snapshots SHALL be daemon-owned:
- a descriptor-based access ACL SHALL grant read only to that center's dedicated owner
  identity;
- there SHALL be no group or other access and no default ACL;
- parents SHALL grant traverse without listing.

Every one of these modes SHALL come from the single declaration shared by the migration and
every runtime site that creates or re-modes these paths.

Best-effort `chmod` is inert on operating systems that do not honor POSIX modes.

#### Scenario: Secret is not recoverable from the vault file
- **WHEN** a credential with a plaintext or base64-encoded secret is written to the vault
- **THEN** the on-disk `.credential-vault.json` is an envelope whose bytes do not contain that secret as cleartext or as base64
- **AND** loading the vault returns the secret to the caller
- **AND** on operating systems that honor POSIX permissions the file mode is restricted

#### Scenario: Legacy vault migrates once and a failed check leaves it in place
- **WHEN** a legacy `schema_version` 1 vault file is loaded and the master key is configured
- **THEN** the records are returned and the file is replaced with an envelope
- **AND** a second load returns the same records without requiring the legacy bytes
- **AND** if the sealed bytes do not decrypt to the same document, the original file is unchanged and the load raises

#### Scenario: Tampered ciphertext does not open
- **WHEN** an envelope's ciphertext bytes are altered
- **THEN** `load_credential_vault` raises and returns no records
- **AND** the error does not include the credential or the file body

#### Scenario: Missing master key fails closed
- **WHEN** a vault file exists and `TINYASSETS_VAULT_MASTER_KEY` is unset
- **THEN** load and write raise
- **AND** no plaintext vault file is written and the existing file is unchanged

#### Scenario: Rotation opens the previous key and reseals with the active key
- **WHEN** an envelope names a retired key id that the provider still holds, and the active key id is different
- **THEN** load returns the records
- **AND** `rewrap_credential_vault` stores the active key id
- **AND** load still returns the records after the retired key is no longer configured

#### Scenario: The broker reads the vault and cannot write it
- **WHEN** the daemon writes a credential to the vault in the production container
- **THEN** the replacement file is `0o640` and group-owned by the vault group, set before
  the replace, so the broker can read it
- **AND** a broker attempt to modify that file or any materialized artifact fails with a
  permission error

#### Scenario: No owner cell can read the vault or another owner's snapshot
- **WHEN** an owner's cell of any class opens the vault, the `.credentials` directory, or a
  snapshot sealed for another owner
- **THEN** the open fails with a permission error
- **AND** the same cell can read its own sealed snapshot and cannot write it or list its
  siblings
