## Why

On 2026-10-11 production exhausted its root disk and refused tool jails. Image retention refused the 225,370-byte release state at its obsolete 64 KiB read limit, while deploys kept pulling images.

## What Changes

- Retain the running daemon, one rollback and explicitly tagged pins; reclaim excess digest-pulled images and dangling layers safely.
- Prune before deploy pulls and refuse insufficient headroom. Surface disk warning at 80% and paging at 90% through uptime monitoring.
- Classify shared-disk failures and return typed, caller-visible grant path refusals.

## Capabilities

### Modified Capabilities
- `uptime-and-alarms`: disk retention, deploy admission and pressure alarms.
- `http-connections-and-outbound-authority`: explicit grant path refusal.
- `core-capability-assurance`: stable shared-disk failure code.

## Impact

Host retention/deploy scripts, authenticated pulse, uptime workflow, broker and connection effector. Owner: Codex; branch: fix/prod-disk-retention-and-grant-errors. Production is read-only during this work; delivery ends at a non-draft infra-change PR.
