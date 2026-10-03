# Tasks: target-architecture (umbrella)

This change tracks slices; each checkbox is one slice. A slice ships as one
or more delivery changes (each ≤12 tasks, one owner, one PR) from the brief in
`design.md` §Slice plan. Its box here is ticked only when all of its changes
are **landed, deployed (`deployed_sha.py --assert-contains`), live-verified and
archived**. Spend marks name the founder approval the slice needs before any
paid resource is created. Dependencies are in `design.md`.

## Start now (parallel)

- [ ] S0 DigitalOcean nested-KVM validation on a short-lived staging droplet;
      decision rule in design §S0. **Founder spend:** staging droplet ~$0.14
      (or approve a quiet-window production benchmark instead). Verify: numbers
      plus commands recorded; D1 and PLAN updated with the decision.
- [ ] S1 Platform-state durability (S1a) and warm standby (S1b): SQLite
      ≥3.51.3, Litestream off-region, off-region backups, weekly DR drill from
      the off-region copy, vault-key escrow, lag alarms, fence-every-host
      standby promotion. The box-inclusive drill lands in S5. **Founder spend:** bucket ~$0–5/mo, standby
      droplet $12–24/mo, Cloudflare LB ~$5/mo. Verify: drill green from
      off-region; promotion drill ≤5 min after detection.
- [ ] S2 Platform state out of the universe directory into the D8a layout
      (`PlatformStatePaths`, §4.16, concern #4258). **Moves delivered by
      `command-center-cutover` (#4262, design E6)**; S2 keeps only the
      refusal, oracle proof and deletion-set tasks. Verify: the three #4258 reproductions fail in the
      Linux oracle; no jail mounts the platform root.
- [ ] S8 Per-command-center owners (a lease + fence each: turn journal,
      reconcile-after-lease, per-command-center handover and barrier) plus one
      platform lease (scheduler, triggers, outbox, metering) behind blue-green
      frontends that queue only the affected command center (S8a
      `execution-owner-lease`, S8b `control-plane-scheduler`); coalescing, cadence
      decay, schema-cutover maintenance protocol. Must land before any second
      writer exists. Verify: scripted deploy loop shows 0 failed requests, no
      duplicate effects and no live turn settled.

## Boxes

- [ ] S3 One accessor for command-center content (local `BoxProvider` driver
      over `universe_files.py`) and the change-generation hot-path cache.
      Depends on S2. Verify: ratchet at 0 direct opens; no turn-latency
      regression.
- [ ] S4 `BoxProvider` complete (bind/wake split, op-id lifecycle, paginated
      and streaming files, per-op auth with placement epochs) + `boxhostd` +
      `boxd` + gVisor driver + reservation ledger + FIFO host admission; tool
      runner and provider launch re-pointed. Depends on S3. Verify: driver contract suite
      green in CI; tool loop end to end through a box on staging.
- [ ] S5 Firecracker driver, checkpoint manifests, online grow, Debian 13
      box host (XFS reflink), consistent per-account-key box backups,
      box-inclusive DR drill, box-host operations (metrics, partition path,
      quarantine runbook, upgrade compatibility), co-tenancy test. Depends on
      S0, S4, and S6 (DEK key service) for backups. **Founder
      spend** if S0 picks bare metal (OVH RISE-S $77/mo). Verify: contract
      suite green on both drivers; restore p95 meets S0's rule.
- [ ] S6 Credential broker extension (sole vault-key holder, in-box
      endpoints for API-key CLIs, file-OAuth copy-back, rotation), per-process
      secret scope, owner-scoped Claude subscription gate (default off).
      Depends on S4, S1 (key escrow). Includes per-command-center DEKs and the plaintext-vault
      migration. Verify: no credential in the loop or any box process except a
      file-OAuth CLI's own token in its owner's box during its run.
- [ ] S7 Thin agent loop in the control plane (HTTP protocols, box handle bound
      at turn start, CLI-in-box only where a credential needs it). Depends on
      S4, S6, S8. Verify: live rendered conversation (`ui-test`); waiting-turn
      memory measured.
- [ ] S9 Box-lifecycle metering and seat + first-come host admission
      replacing the 4-run pool and tool slots. Depends on S4 (S5 for
      Firecracker metering). **Founder decision:** whether to add a
      compute-hour budget and spare lane (its own change, only if adopted).
      Verify: first-come fairness holds under a 20-agent fan-out.

## Shared stores and cutover

- [ ] S10 Postgres for catalog/ledger/inbox/market (`TransactionalStore`),
      outbox, identity map, `home_cell` + signed cell claim + ownership
      generation + ingress dedup (one cell); the cell-move protocol is
      specified and built at the second-cell trigger. **Founder spend** only if managed
      Postgres ($15.15–30.30/mo; self-hosted $0). Verify: a stale-generation
      request is refused, a duplicate webhook runs once, a lost-ack outbox
      delivery applies once.
- [ ] S11 Cutover from the D8a user-content folders to sealed boxes: derived inventory, locked
      verified run, flip, rollback rehearsal, export and deletion through the
      box, retire bwrap jails and host-path drivers. Depends on the cutover
      (#4262) or S2, plus S1, S3, S4/S5 (per S0's decision), S6, S7, S8, S9,
      S10. Runs in its own declared freeze window. Verify: every command center serves from its box; public canary
      `--assert-handles` and a rendered conversation green; DR drill restores
      boxes; then sync specs and archive this change.
