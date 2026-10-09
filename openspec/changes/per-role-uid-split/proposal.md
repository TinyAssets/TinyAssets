# Per-owner isolation, one clean cutover

**Founder decision 2026-10-08 (README § Direction line 5, PR #4558):** the isolated path is the
only path. It ships with one migration in a short maintenance window after a full backup. Cross-owner
app checks that the OS now enforces are deleted in the same change. This replaces the ~13
switched-off slices (L0–L12), the reversible migration and the activation lanes A–G. We have one
real user plus a handful of testers, so the reversible-migration machinery and the dual-path
off switches were insurance we don't need. They also made the work take weeks and grew the
codebase.

## Why

The credential broker authenticates callers by kernel uid (`SO_PEERCRED`). In production every
role runs as uid 1001: the daemon, every engine and provider child, and every workspace worker.
So any child can act as its owner on the broker, and one owner's child is the same kernel
principal as every other owner's. The broker also cuts a model round from about 29 MiB (one
Python proxy worker) to about 100 KiB (one stream). This change is therefore both the
security boundary and the memory headroom. It unblocks outbound MCP attach
(`broker-streaming-contract`, then `connect-anything-ladder`).

## What Changes

- **End state, no alternative.**
  - The broker runs as 1002.
  - The daemon stays 1001, with no capabilities.
  - Every owner gets a permanent dedicated UID/GID (300001–399999, founder D60). Every
    owner-scoped child runs as that pair inside an owner cell, started by the bounded mapper.
  - New centers are admitted at runtime from the broker's admission log.
  - Startup is the D70 bootstrap, every boot.
  - Nothing reads `TINYASSETS_CREDENTIAL_BROKER`, and there is no legacy spawn, reader or
    proxy-worker path.
- **One forward-only migration.**
  - It runs once, offline, as a one-shot container with writers stopped, after a full
    snapshot of the data volume.
  - Each step converges the volume to a target computed from the volume itself, so a crash is
    fixed by running it again.
  - There is no reverse migration, journal, quarantine or old-image probe.
  - Rollback is: restore the snapshot and redeploy the previous image.
- **Deletions.** The switch plumbing and every legacy branch go. The few app-level checks the
  kernel now enforces go too (`design.md` § 5).
- **Folds in `owner-dynamic-admission`.** Its runtime admission requirements move here. Its
  restart contract (DA7) and its "inert while the bounded client is absent" rule (DA8) are
  dropped, because startup no longer migrates and there is no legacy path. That change is
  deleted.

## Impact

- Three PRs (`design.md` § 6): the migration with its runbook (inert), the cutover (merged
  inside the window), then the deletions.
- These are superseded (listed, not closed):
  - U1 #4523 `feat/per-role-uid-split`
  - U2 #4509/#4510 `feat/per-role-uid-split-migration`
  - the stacked base of #4512
- #4556 (L3 consumers) lands as is; the cutover deletes its switch branches.
- Specs: new capability `runtime-process-roles`; `credential-vault` modified.
