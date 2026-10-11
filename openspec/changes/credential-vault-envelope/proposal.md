## Why

`.credential-vault.json` is plaintext JSON. Base64 fields are an encoding, and the deposit path said envelope encryption was deferred. A copy of that file is the credentials.

## What Changes

- Seal the vault file as a versioned AES-GCM envelope: a random data key wraps the document, and a master key wraps the data key.
- Default the master key to `TINYASSETS_VAULT_MASTER_KEY` on the daemon. A pluggable provider can be replaced by a managed store later.
- Migrate a legacy vault on load only after a decrypt round-trip matches. Fail closed when the key is missing. Rotate by key id.
- Keep the in-memory credential records, the non-secret write summary, and the broker read / daemon write file modes.

## Capabilities

### Modified Capabilities

- `credential-vault`: the vault file is an envelope. Filesystem permissions still apply. Materialized CLI artifacts stay outside the envelope.

## Impact

Owner: this change. Branch: `cursor/vault-envelope-encryption-fd9b`. One draft PR against `main`. No production workflow or secret-value edits. The deploy owner must set the master key before this build runs, or vault access fails closed and legacy files are left as they are.
