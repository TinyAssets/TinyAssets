## MODIFIED Requirements

### Requirement: As-Built Storage Protection Is Filesystem Permissions Only

The vault file and any materialized credential artifacts (for example a Codex `auth.json` or a Claude config directory) SHALL be persisted as unencrypted content on disk. The only at-rest
protection SHALL be POSIX ownership, mode and ACL.

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

As-built limitation: there is no encryption at rest, no cipher and no key management. Base64
fields such as `token_b64` and `secret_b64` are an encoding, not encryption. Best-effort
`chmod` is inert on operating systems that do not honor POSIX modes.

#### Scenario: Secret is stored in cleartext under a restricted file mode
- **WHEN** a credential with a plaintext or base64-encoded secret is written to the vault
- **THEN** the on-disk `.credential-vault.json` contains that secret as recoverable cleartext
  (directly or base64-decodable) with no ciphertext layer
- **AND** on operating systems that honor POSIX permissions the file mode is restricted,
  while the content itself remains unencrypted

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
