## Context

`load_credential_vault` used to `json.loads` the vault file. `write_credential_vault` returned the same record dicts. The broker can read the file and must not write it (`BROKER_READ_GID`, `VAULT_FILE_MODE`). That interface stays. The bytes on disk change.

## Goals / Non-Goals

**Goals:**

- Authenticated encryption at rest for the vault file.
- A version field, a key id, and a provider the process can swap.
- Migration that is idempotent and does not replace the file until the seal decrypts to the same bytes.
- Rotation without a plaintext window.
- Fail closed. No ephemeral key. No plaintext fallback. Errors do not contain secrets.

**Non-Goals:**

- Encrypting materialized `.credentials` files or per-launch snapshots. Those copies exist so a CLI can read them. They stay on the filesystem-permission rules.
- Choosing a managed secret store. The env provider is the default. TODO(owner): a store implements `VaultKeyProvider` and is installed with `set_vault_key_provider`.
- Editing deploy workflows or placing a key value in the repo.

## Decisions

1. **Envelope, not a new record schema.** The inner document is still `schema_version` 1 plus `credentials`. Callers keep today's dicts. The outer object is `format=tinyassets-credential-vault-envelope`, `version=1`, `key_id`, nonces, `wrapped_dek`, `ciphertext`.
2. **AES-GCM via `cryptography`.** One random 32-byte data key per seal. The master key wraps that data key. Both uses have associated data. The document is bound to the command-center directory name, so a file copied into another command center does not open. The full path is not bound, so a restore that keeps the directory name still opens.
3. **Environment provider.** `TINYASSETS_VAULT_MASTER_KEY`, `TINYASSETS_VAULT_MASTER_KEY_ID` (default `1`), `TINYASSETS_VAULT_PREVIOUS_MASTER_KEYS` (`key_id=material` comma-separated). Documented in `docs/reference/environment-variables.md`. The deploy owner sets them before this build serves traffic.
4. **Migration.** Legacy JSON (a list, or an object with `schema_version` or `credentials`) is sealed, decrypted, and compared. The file is replaced only when the bytes match and the on-disk bytes are still the ones that were read. A vault mutation lock, not provider-assignment admission, covers that replace: launches hold admission shared while they read. Permission to replace is required; a reader that cannot write still receives records and leaves the legacy file for a writer.
5. **Rotation.** New seals use the active id. Old envelopes open with `key_by_id`. `rewrap_credential_vault` reseals. The next ordinary write does too.
6. **Fail closed.** Any existing vault file requires a usable master key before its bytes are treated as credentials. A missing file is still an empty list.

## Risks / Trade-offs

- **Deploy before the key is set.** Vault reads and writes raise. Legacy files stay on disk, still plaintext, until a configured process migrates them. Set the key, restart, then deploy.
- **Key loss.** Envelopes sealed with that key do not open. There is no plaintext backup.
- **CLI artifacts.** `auth.json` and similar files are still plaintext under the existing modes.
- **Engine UID.** Filtering the key out of child environments does not hide `/proc` from a process that shares the daemon uid. That limit is already documented for other daemon secrets.
- **Commented template lines.** `deploy/tinyassets-env.template` gains commented unset names so the engine-inheritance inventory covers them. No workflow, renderer, or secret value changes.

## Migration Plan

1. Owner generates `TINYASSETS_VAULT_MASTER_KEY`, sets id `1` in `/etc/tinyassets/env`, re-renders daemon env, restarts the daemon.
2. Deploy this build.
3. The next load of each legacy vault migrates it.
4. Rollback of the code before migration: legacy files are unchanged, so the previous build still reads them. Rollback after migration: the previous build cannot read envelopes. Keep this build, or restore a pre-migration backup of the vault files. Do not deploy until the key is in the daemon environment.

## Open Questions

- TODO(owner): which managed secret store, if any, replaces the environment provider.
