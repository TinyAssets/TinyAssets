# credential-vault (delta)

## ADDED Requirements

### Requirement: The vault stores delegations, never site passwords

The vault SHALL hold only credentials that an issuer granted and can revoke, in
this order of preference:

1. a remembered browser session (cookies and site storage) from the owner's own
   sign-in, held by browser custody;
2. OAuth access and refresh tokens from a self-describing sign-in or an
   owner-owned developer app;
3. an API key, personal access token, or developer-app client secret the owner
   pasted;
4. a model subscription's own auth bundle.

The platform SHALL NOT store a website password, an MFA/TOTP seed, a recovery
code or a passkey private key. A sign-in that needs one SHALL happen in the
protected live view, where the owner types it into the site's own page or their
own password manager fills it, and only the resulting session is kept.

#### Scenario: A sign-in asks for a password

- **WHEN** a browser connection reaches a password, OTP or passkey prompt
- **THEN** the owner completes it in the protected live view, the agent sees no
  frame of the private input, and the vault stores only the resulting session

#### Scenario: A deposit carries a password field

- **WHEN** any deposit path receives a record whose fields name a website
  password, TOTP seed or recovery code
- **THEN** the write is refused before storage with a reason naming browser
  sign-in as the alternative

### Requirement: One key hierarchy, held only by the broker

Every stored credential, including browser session state, SHALL be sealed with
an AEAD cipher under its command center's data key (DEK). Each DEK SHALL be
wrapped by its owning account's key-encryption key (KEK), and each account KEK
by the cell's root key. Every layer SHALL record the id of the key that sealed
it. The associated data of each sealed record SHALL bind the account, command
center, connection or record id, and record generation, so a ciphertext copied
to another owner, center or slot does not open.

Only the broker process SHALL hold or unwrap key material. The daemon, the agent
loop, provider children, engine servers and every box SHALL receive neither
keys nor plaintext credentials; they reach a credential only through a broker
operation that applies it at egress. The daemon SHALL pass deposits to the
broker and SHALL NOT read sealed records.

#### Scenario: A ciphertext is moved to another owner

- **WHEN** a sealed record is copied into another account's or command
  center's store
- **THEN** opening it fails authentication and no plaintext is returned

#### Scenario: A daemon process environment is read

- **WHEN** the environment and open files of the daemon and its children are
  inspected
- **THEN** no root key, KEK, DEK or credential plaintext is present

### Requirement: The root key provider is a seam

The root key SHALL be reached only through one provider interface with two
operations, wrap and unwrap of account KEKs. The sealed record and wrapped-key
formats SHALL NOT depend on which provider is installed. Two providers SHALL be
supported: a local root held in a broker-only key file outside the backed-up
data volume, and a cloud KMS key that never leaves the KMS. A missing or
unreachable provider SHALL fail closed: nothing is written as plaintext and no
ephemeral key is generated.

#### Scenario: The provider changes

- **WHEN** the founder moves the root from the local provider to a cloud KMS
- **THEN** only the account KEK wraps are rewritten and every sealed record
  still opens

### Requirement: The root key is escrowed and restore is proven

The root key, or the KMS key's identifier and access, SHALL be escrowed outside
the production host and outside every data backup, under the escrow holder
recorded by the vault-key escrow design. Data backups SHALL carry only sealed
records and wrapped keys. The DR drill SHALL restore from off-region copies
with the escrowed root and open one restored credential record; a failure to
open SHALL fail the drill.

#### Scenario: The drill restores without the root

- **WHEN** a restore runs with data backups but no escrowed root
- **THEN** the drill fails and names the missing root key

### Requirement: An unopenable credential asks for reconnect

The broker SHALL refuse an unopenable sealed record (missing key, unknown key
id, or failed authentication) for that one credential only, with a named
`reconnect_required` reason, and the platform SHALL raise the ordinary connect
ask for that connection. Other credentials and the daemon SHALL keep working.
The record SHALL NOT be returned, rewritten or deleted.

#### Scenario: One record is corrupted

- **WHEN** one connection's sealed record fails authentication
- **THEN** that connection's calls are refused with `reconnect_required`, its
  owner sees a reconnect ask, and every other connection still works

### Requirement: Keys rotate without downtime

Root and KEK rotation SHALL rewrap the layer below without touching sealed
records. DEK rotation SHALL reseal a center's records under a new DEK while the
old DEK still opens records that name it. Every rotation SHALL be an explicit,
recorded operation, and a retired key SHALL be destroyed only after no record
or wrap names it.

#### Scenario: The root rotates

- **WHEN** the root key rotates
- **THEN** every account KEK is rewrapped under the new root, every credential
  still opens, and the old root is destroyed only after no wrap names it

### Requirement: Disconnect revokes and erasure shreds

Disconnecting a connection SHALL call the issuer's token revocation endpoint
(RFC 7009) when the issuer advertises one, then delete the sealed record; the
owner SHALL be told when the issuer offered no revocation. Account erasure
SHALL destroy the account KEK after the deletion order in target-architecture
D12, so every backup copy of that owner's credentials becomes unreadable
without touching other owners.

#### Scenario: The owner disconnects an OAuth connection

- **WHEN** the owner disconnects a connection whose issuer advertises a
  revocation endpoint
- **THEN** the refresh token is revoked at the issuer and the sealed record is
  deleted

#### Scenario: An account is erased

- **WHEN** account erasure completes
- **THEN** that account's KEK is destroyed and no backup of its credentials can
  be opened, while other accounts' credentials still open

### Requirement: One cutover from the legacy vault

The migration from `.credential-vault.json` SHALL run once, in a maintenance
window, after a verified full backup. It SHALL seal every legacy record into the
broker store, open each sealed record and compare it with the legacy bytes,
and remove the legacy file only after the comparison passes. The legacy reader,
the legacy writer and the vault-group read path SHALL be deleted in the same
change, so the sealed store is the only path.

#### Scenario: A legacy record does not round-trip

- **WHEN** a sealed record does not match its legacy bytes
- **THEN** the migration stops before removing any legacy file and reports the
  command center, with no record written as plaintext elsewhere

## REMOVED Requirements

### Requirement: As-Built Storage Protection Is Filesystem Permissions Only

**Reason**: Replaced by "One key hierarchy, held only by the broker". File
modes remain defence in depth, but cleartext credentials no longer reach disk,
backups or the daemon.

**Migration**: "One cutover from the legacy vault" seals every legacy record
and deletes the legacy file and the vault-group read path.
