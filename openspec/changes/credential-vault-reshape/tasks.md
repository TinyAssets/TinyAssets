# Tasks: credential-vault-reshape

**Tier:** floor-class (credential custody). One PR for the cutover. One
cross-family review round before merge.

This change is target-architecture S6 task 1. Mark it done there when this
lands.

## 0. Before code

- [x] 0.1 #4611 closed. Its head `dbe7a4675fc` stays on
  `cursor/vault-envelope-encryption-fd9b` for reuse (design §5).
- [x] 0.2 Founder decisions, 2026-10-11:
  - AWS KMS root, with no app-held key and no offline copy;
  - existing backups are rewritten at cutover.
- [x] 0.3 Vault row added to the Shape registry in `docs/architecture.md`.
- [ ] 0.4 Founder runs the AWS vault key steps in `docs/host-actions.md`:
  - the KMS key;
  - the IAM user and its access key;
  - the DR drill's OIDC role;
  - the GitHub `production` and `dr-drill` environment secrets.

## 1. Key service in the broker

- [ ] 1.1 `RootKeyProvider` seam (`generate`, `unwrap`, `wrap`) and the AWS
  KMS adapter as its only production provider:
  - every call carries the encryption context;
  - an in-memory fake for tests, refused in production.
- [ ] 1.2 Account KEK and command-center DEK stores:
  - wrapped, with provider id and key ids on every layer;
  - unwrapped KEKs cached in broker memory only.
- [ ] 1.3 Sealed record format, reusing the #4611 envelope fields:
  - AES-256-GCM, random 96-bit nonce;
  - AAD = account, command center, record id and generation.
  - Port `tests/test_vault_envelope.py` (migration, idempotence, tamper,
    missing key, rotation, cross-center).
- [ ] 1.4 Broker operations: deposit, open at egress, delete, and a non-secret
  list. Move the deposit paths and the OAuth refresh write-back onto them. The
  daemon no longer reads or writes credential files.
- [ ] 1.5 Refuse password, TOTP-seed and recovery-code fields at deposit.
- [ ] 1.6 Credential delivery:
  - deploy installs `TINYASSETS_VAULT_KMS_KEY_ARN`,
    `TINYASSETS_VAULT_AWS_ACCESS_KEY_ID` and
    `TINYASSETS_VAULT_AWS_SECRET_ACCESS_KEY` into a broker-only `0400` file;
  - add them to `DAEMON_FORBIDDEN_ENV` and the engine strip list;
  - `daemon_env_scoped` covers them.

## 2. Browser custody joins the hierarchy

- [ ] 2.1 Reseal browser state under the center DEK.
- [ ] 2.2 Retire `/var/lib/ta-broker/browser-vault` and the
  `tinyassets-browser-keys` volume.

## 3. Faults and lifecycle

- [ ] 3.1 Per-connection refusals, never a whole-vault failure:
  - an unopenable record returns `reconnect_required` and raises the connect
    ask;
  - an unreachable KMS returns `key_service_unavailable`, which is retryable
    and raises no ask.
- [ ] 3.2 A broker KMS health probe wired into the uptime alarm.
- [ ] 3.3 Rotation:
  - a KEK rewrap sweep after KMS material rotation;
  - DEK reseal;
  - retired keys destroyed only when nothing names them.
- [ ] 3.4 Disconnect calls RFC 7009 revocation when discovery advertises it,
  and tells the owner when it does not.
- [ ] 3.5 Account erasure destroys the wrapped KEK after the D12 order. The
  restore tombstone list refuses to re-import it.

## 4. Backup and drill

- [ ] 4.1 `deploy/backup.sh` asserts that no backup contains an unwrapped key,
  a legacy vault file or a raw browser key.
- [ ] 4.2 The DR drill assumes the OIDC role, then opens one restored
  credential and one restored browser session.

## 5. Cutover (one maintenance window)

- [ ] 5.1 Live migration:
  - start from a verified legacy backup and a green KMS probe;
  - seal, open and compare each record, then remove the legacy file;
  - on any mismatch, stop with nothing removed.
- [ ] 5.2 Delete, in the same change:
  - the legacy reader and writer;
  - the vault-group read path and its `VAULT_FILE_MODE` group grant;
  - the raw browser key code.
- [ ] 5.3 Backup rewrite, in the order in design D8:
  1. inventory and manifest;
  2. a fresh sealed baseline, verified by restore;
  3. the newest legacy archive as a canary, proven by restore and scan;
  4. then one archive at a time, each verified before its original, its
     noncurrent versions and its GitHub asset are deleted;
  5. stop and resume from the manifest on any failure;
  6. a final whole-destination scan.
- [ ] 5.4 Verify:
  - `deployed_sha.py --assert-contains`;
  - one naive-user pass: an existing connection works, a disconnect revokes,
    and a browser session survives a restart;
  - the DR drill is green;
  - the rewrite manifest is attached.
- [ ] 5.5 Spec sync and archive. The Shape registry row moves to converged.
