## MODIFIED Requirements

<!-- founder decision 2026-10-05: fold + build with probes.
     Workspace ACLs do not apply to vault or broker state. -->

### Requirement: As-Built Storage Protection Is Filesystem Permissions Only

The vault file and any materialized credential artifacts (for example a Codex `auth.json` or a Claude config directory) SHALL be persisted as unencrypted content on disk, and the only at-rest protection SHALL be a best-effort POSIX file mode. Where every role shares one uid — a developer host, a desktop install — that mode SHALL be `0o600` for the vault file and secret files and `0o700` for the `.credentials` artifact directory. Where the per-role uid split is deployed (`runtime-process-roles`), the owner uid SHALL remain the only writer and the mode SHALL be `0o640` for the vault file and secret files with their directories `2750`, group-owned by the vault group whose members are the owner and broker uids. That group SHALL be set on the temporary file before the atomic replace rather than inherited from the directory, because the vault's directory is the command-center root and belongs to the work group; a vault that inherited that group would be readable by every engine and provider child. Per-launch credential snapshots and the platform runtime directory that holds them SHALL instead take the work group, at `2750` with files `0o440`, because the engine/provider child reads its own snapshot. Every one of these modes SHALL come from a single declaration shared by the ownership migration and each runtime site that creates or re-modes these paths. The split's result is strictly tighter than the single-uid case, where every child process shares the owner's uid and can read a `0o600` vault.

As-built limitation: there is no encryption at rest, no cipher, and no key management; base64 fields such as `token_b64` / `secret_b64` are an encoding convention, not encryption, and best-effort `chmod` is inert on operating systems that do not honor POSIX modes. A layered cipher/store design exists only as an approved future design and is not present in the code on `main`.

Under D60's bounded owner launcher, the following replaces the shared work-group
snapshot rule above: publication SHALL match the custody principal's broker
UID/GID to the protected migrated center label before reading material. Sealed
snapshots remain daemon-owned protected metadata. Descriptor-based POSIX ACLs
grant only that dedicated owner read/traverse, with no group/other access, write
access, or inherited default ACL. Parents grant traverse without listing. Missing
identity, unsupported ACLs, and unmigrated centers refuse without shared-group fallback.

#### Scenario: Repreparation cannot restore another owner's snapshot access
- **WHEN** snapshot parents contain legacy shared-group or foreign named/default
  ACL entries and the daemon prepares and reprepares a dedicated owner's snapshot
- **THEN** only that owner can read its exact snapshot and lock its read-only lock
- **AND** another owner, the legacy work-group engine, and broker cannot read it
- **AND** owner writes and sibling listing fail while daemon cleanup succeeds

#### Scenario: Secret is stored in cleartext under a restricted file mode

- **WHEN** a credential with a plaintext or base64-encoded secret is written to the vault
- **THEN** the on-disk `.credential-vault.json` contains that secret as recoverable cleartext (directly or base64-decodable) with no ciphertext layer
- **AND** the write sets the file mode to `0o600` on operating systems that honor POSIX permissions, while the content itself remains unencrypted regardless of the mode

#### Scenario: Under the uid split the broker reads the vault and cannot write it

- **WHEN** the per-role uid split is deployed and the owner writes a credential to the vault
- **THEN** the replacement file is mode `0o640` and group-owned by the vault group, assigned before the replace, so the broker uid can read it and no engine or provider child can
- **AND** an attempt by the broker to modify that file or any materialized artifact fails with a permission error

#### Scenario: Under the uid split no engine or provider child can read the vault

- **WHEN** the per-role uid split is deployed and an engine or provider child (uid 1003) opens the vault file or the `.credentials` artifact directory of any command center
- **THEN** the open fails with a permission error, because neither the child's uid nor its groups are granted on either path

#### Scenario: Workspace ACL migration does not widen credential access

- **WHEN** workspace trees receive uid-1001 access/default ACLs and shared stores lose other-read during the role migration
- **THEN** vault, materialized credential and broker-state sets retain their separate declared permissions and link-refusal rules, without inheriting ta-work ACLs
- **AND** the production-image oracle proves each actual engine class remains denied the vault, while an atomic owner deposit remains readable but not writable by the broker
