# Tasks: credential-vault-reshape

**Tier:** floor-class (credential custody). One PR for the cutover. One
cross-family review round before merge.

**Depends on:**
- the founder decisions in `design.md` §6;
- the escrow holder from `docs/design-notes/2026-10-02-vault-key-escrow.md`.

This change is target-architecture S6 task 1. When this lands, mark that task
done there.

## 0. Before code

- [ ] 0.1 Close PR #4611 as superseded by this change. Do not set
  `TINYASSETS_VAULT_MASTER_KEY` on the host.
- [ ] 0.2 Founder picks:
  - the root provider (local or KMS);
  - the escrow holder;
  - what happens to pre-cutover backups.
- [ ] 0.3 Shape registry:
  - Add one row once PR #4619 merges: "One credential custody (ADR-012,
    D12/S6) | broker-only key hierarchy, root -> account KEK -> center DEK |
    cleartext `.credential-vault.json` read via vault group; separate raw
    browser keys; daemon-env master key proposed in #4611 | drifted |
    `credential-custody` (planned) | C6".
  - Or fold it into S27.

## 1. Key service in the broker

- [ ] 1.1 Root provider seam (`wrap`/`unwrap` of account KEKs). Ship the local
  provider. Missing or unreachable fails closed.
- [ ] 1.2 Account KEK and command-center DEK stores, wrapped, key ids on every
  layer. Unwrapped KEKs are cached only in broker memory.
- [ ] 1.3 Sealed record format:
  - AES-256-GCM with a random 96-bit nonce;
  - AAD = account, command center, record id, generation;
  - salvage the #4611 envelope fields.
- [ ] 1.4 Broker operations: deposit, open-at-egress, delete, list (non-secret
  summary). Move the deposit paths and OAuth refresh write-back onto them. The
  daemon no longer reads or writes credential files.
- [ ] 1.5 Refuse password, TOTP-seed and recovery-code fields at deposit.

## 2. Browser custody joins the hierarchy

- [ ] 2.1 Reseal browser state under the center DEK.
- [ ] 2.2 Retire `/var/lib/ta-broker/browser-vault` and the
  `tinyassets-browser-keys` volume.

## 3. Faults and lifecycle

- [ ] 3.1 An unopenable record returns `reconnect_required` for that
  connection and raises the connect ask. Other connections keep working.
- [ ] 3.2 Explicit rotation operations for the root, KEKs and DEKs. Retired
  keys are destroyed only when nothing names them.
- [ ] 3.3 Disconnect calls RFC 7009 revocation when discovery advertises it,
  and tells the owner when it does not.
- [ ] 3.4 Account erasure destroys the account KEK after the D12 order, with
  the receipt recorded.

## 4. Escrow, backup and drill

- [ ] 4.1 Install the root from escrow through deploy (set-once, the #4260
  precedent).
- [ ] 4.2 `deploy/backup.sh` asserts that no backup contains the root or an
  unwrapped key.
- [ ] 4.3 The DR drill opens one restored credential and one restored browser
  session with the escrowed root.

## 5. Cutover

- [ ] 5.1 Migration:
  - run it in a maintenance window, after a verified full backup;
  - seal, open and compare each record, then remove the legacy file;
  - on any mismatch, stop with nothing removed.
- [ ] 5.2 Delete, in the same change:
  - the legacy reader and writer;
  - the vault-group read path and `VAULT_FILE_MODE` group grant;
  - the raw browser key code.
- [ ] 5.3 Carry out the founder's decision on pre-cutover backups.
- [ ] 5.4 Verify:
  - `deployed_sha.py --assert-contains`;
  - one naive-user pass: an existing connection still works, a disconnect
    revokes, and a browser session survives a restart;
  - the DR drill is green.
- [ ] 5.5 Spec sync and archive.
