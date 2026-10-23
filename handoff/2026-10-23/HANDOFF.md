# Session handoff, 2026-10-23 (TinyAssets)

Read first: `openspec/changes/owner-daemon-in-box/research.md` and the "Target shape (binding)" table at the top of its
`design.md`; the C6 tracker is section 8 of `openspec/changes/owner-daemon-in-box/tasks.md` (PR #5226).

Lane briefs and results live in
`C:\Users\Jonathan\AppData\Local\Temp\claude\C--Users-Jonathan-Projects-TinyAssets\a45e15fc-104d-4ceb-be93-dd63605ef297\scratchpad\`
(`brief-*.md`, `go-*.md` = brief + common constraints, `result-*.md`, `evidence-*.md`, `receipt.sh` posts a lead
Drain-Review receipt and cites it at the top of the PR body).

## Production (as of 2026-10-23 ~04:20 UTC)

- Running the restored last-good state: old image `d57f2894463b` (containers), old box host and box image
  `harness-d57f…`, with ~89 hotfix files re-placed from the ledger plan (`/root/restore-plan.json`) plus the sign-in fix
  (`/root/workos_provider.py.hotfix`) and the broker's `TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED`.
- Both test accounts reply (main …MEV7 via Claude subscription; canary …QY4S via OpenRouter).
- Hotfix ledger records are ARCHIVED (status archived, superseded_by #5227); two config-only records moved to
  `hotfixes/config-only-archived/`. Backups: `hotfixes.pre-archive-20261023T031521Z`, `restore-<stamp>/`.
- `deploy-prod.yml` is disabled; the `production` environment accepts deploys only from `main`.

## Incident 2026-10-23 03:30-04:15 UTC

Deploy of main `eb582d82d08` (#5227): canary 503 ("boxhostd connection limit exceeded" during post-drain snapshot
work); rollback restored containers only, leaving a mixed bundle; broker.env rewrite dropped the HTTP flag. Fix is
PR #5238 (fix/deploy-box-bundle, worktree `wf-deploy-fix`), round 2 done; final xhigh re-review running.

## In flight

| Item | Where | State |
|---|---|---|
| Deploy fix #5238 | wf-deploy-fix | final re-review (result-rereview-deploy.md) |
| N10+N5 #5245 | wf-n5-n10 | floor review (result-review-n5.md) |
| S10 #5244 | wf-s10-tables | fix round resumed from WIP commit (result-s10-fix.md) |
| N1 #5235 | wf-n1-impl | finishing on durable send (result-n1-cont.md) |
| Merge queue | GitHub | approved + receipted: #5226 #5228 #5229 #5230 #5231 #5232 #5233 #5237 #5239 #5240 #5241 #5242 #5243 #5246 (#5246 fixes main's queue CI) |
| Vault #5234 | wf-vault-kms | needs review (CLI logins lent into boxes per run vs ADR-022) |

## Next

1. #5238 agrees -> merge -> re-deploy main (founder approved) -> one-shot repair
   (`python -m tinyassets.cutover_assignment_repair --owner-root <vol> --ledger <broker outbound.db> --stopped`, boxes
   stopped; founder approved) -> confirm hotfix ledger empty -> naive-user pass on both accounts -> close C6.
2. Then: S2 executor merge, C12 slice 1, N3/N9 build, N6, 6a part 2 deletions. Run at most ~4 agents at once (memory).

## Founder decisions this session

N6 undo = sleep snapshots + owner-requested checkpoints. N5 = open by default, allowlist/ask opt-in, owner-connected
destinations allowed only when committed via the owner session, ask approvals become standing entries. N1 = one shared
tap-to-install app per channel; door reads only the installation identity. Disconnect leaves nothing (no per-user CLI).
