# Tasks: one-box-per-owner (convergence item C6)

Each task below is one PR, and each PR ships on its own. Every PR:

- deletes the old path in the same PR;
- states its measured `git diff --shortstat` net for core code;
- does not merge if that net is positive.

A task marked **Floor** gets one cross-family `peer-agents` round, with
floor and correctness findings answered `AGREE` or `DISAGREE_EVIDENCE`
(AGENTS loop 4). Sandbox and process-limit work runs in
`scripts/linux_oracle.py`; a skip is not a pass. Done for each PR means its
sha is asserted deployed, one naive-user pass is made through the app agent,
and the spec is synced.

## 0. Contract

- [x] 0.1 ADR-019, plus the status line in ADR-012, the proposal, the
      design and the spec deltas. Verify:
      `npx openspec validate one-box-per-owner --strict`.
- [x] 0.2 Registry row N10, "Owner-only policy layer", is added by the
      lead in a separate PR. This change is its build path (P1, guard
      `policy-writers`). P9 marks N10 converged together with S8, S10 and
      S27. The registry's figure of 34 `role_*` cells is 33 on main; P9
      corrects it when it updates S8.

## 1. Pull requests (in order)

- [ ] 1.1 **P1 Owner policy layer.** **Floor:** authority and money. Depends
      on nothing; runs beside C3.
      - **Builds:**
        - `tinyassets/broker/policy.py`: `OwnerPolicy`, a generation and a
          digest, in `.platform/accounts/<id>/policy.db` (broker uid, 0600).
        - `/app/api/policy`, which carries the interactive owner-session
          token. The broker verifies the token itself.
        - Broker enforcement on today's connector, HTTP and spend paths:
          floor, then policy, then ask, hold or deny, then a receipt.
        - The read-only projection, and `ta policy show` and
          `ta policy propose` (the proposal becomes an owner request).
        - The guard `policy-writers`.
      - **Migration:** copy each center's frozen `rules.db` and grants into the
        owner's `policy.db` at broker bootstrap or center admission, verified
        by count and digest; refuse and report unfrozen sources.
        Old files stay read-only until P6.
      - **Deletes:** `agent_rules.py`, `approval_scopes.py`,
        `broker/http_policy.py`, and the rule routes in
        `onboarding/__init__.py` that it replaces.
      - **Verify:** the injection test in design §12 (policy part) on
        today's runtime; an owner write without the token is refused.
      - **Net:** about −550.
- [ ] 1.2 **P2 Box driver, admission once, tool family.** **Floor:**
      cross-user and data. Depends on P1. **This PR holds the one
      maintenance window.**
      - **Builds:**
        - `boxhostd` as a systemd service and the gVisor driver behind
          `BoxProvider`, re-keyed per owner (`bind(owner)`).
        - `boxd`, moved from the `boxes/local.py` core.
        - The read-only harness rootfs.
        - Per-owner project quota.
        - `admit` once at session entry.
        - `read`, `write`, `edit`, `bash`, git, code nodes, decoding and
          media running in the box. Box egress uses the existing
          per-center relay until P5.
      - **Window runbook:** the design §11 steps, with a verified full
        backup first.
      - **Deletes:**
        - role modules 1-13 in the design §8 table;
        - the `universe_tools.py` jail path;
        - the `node_sandbox.py` bwrap launcher;
        - `jail_disk.py` and `sandbox/`;
        - `role_run_probe.py` and `role_writer_probe.py`.

        It adds `scripts/box_acceptance.py`.
      - **Verify:** the cross-user check in design §12 on `runsc` in the
        Linux oracle; a production-copy turn runs real tools in the box.
      - **Net:** about −4.2k.
- [ ] 1.3 **P3 Sessions in the box.** **Floor:** credential custody.
      Depends on P2, C3 (`one-model-call-path`) and C8 (`session.start`).
      - **Builds:**
        - `session.start` runs the loop as a process in the owner's box.
        - Model calls leave through the broker gate (`model_call`).
        - Child sessions inherit the parent's handle and allocation.
        - Resident processes run in the box (folds
          `resident-agent-processes`).
        - Native CLIs run as external-agent packages in the box, using
          the broker's in-box endpoint.
        - A session survives app close and daemon deploys.
      - **Deletes:**
        - role modules 14-20;
        - `providers/provider_jail.py`;
        - `role_provider_cell_probe.py`, `role_provider_turn_proof.py` and
          `role_chat_probe.py`.

        It also folds the open tasks of `remote-box-ta-bridge` and
        `remote-box-extension-runtime`.
      - **Verify:** the G1 done check in design §12, live.
      - **Net:** about −1.2k.
- [ ] 1.4 **P4 Browser and the N4 view hook.** **Floor:** cross-user view.
      Depends on P2.
      - **Builds:** Chromium in the box, and `BoxProvider.view` relayed
        by `boxhostd`, which only the owner door can open.
      - **Deletes:** role modules 21-22, `browser_cell.py`,
        `browser_egress.py` and `role_image_oracle.py`.
      - **Verify:** a non-owner session cannot open the view; no typed
        secret appears in a transcript or a receipt.
      - **Net:** about −400.
- [ ] 1.5 **P5 Egress gate (the N5 enforcement point).** **Floor:**
      exfiltration. Depends on P1 and P2.
      - **Builds:** one per-box relay at the broker: the floor, then the
        owner egress policy (`open`, `allowlist` or `ask`), then grants.
        Git over HTTPS uses this relay with `broker/git_http` credentials.
      - **Deletes:** role modules 23-24, `universe_egress.py`,
        `git_egress.py` and `role_http_fixture.py`.
      - **Verify:** an unlisted host is denied under `allowlist`; `ask`
        holds the connection and raises an owner request; SMTP is refused.
      - **Net:** about −700.
- [ ] 1.6 **P6 Lifecycle, the snapshot hook, the mapper retired.**
      **Floor:** data loss. Depends on P3, P4 and P5.
      - **Builds:**
        - suspend and resume, and checkpoint and restore across a
          `boxhostd` restart, falling back to a cold boot plus the journal;
        - `BoxProvider.snapshot` and `restore`, with one automatic
          snapshot per session;
        - `destroy` under the owner-wide deletion fence;
        - the daemon container starts unprivileged.
      - **Deletes:**
        - role modules 25-33;
        - `owner_launcher_client.py`, `owner_socket.py`,
          `broker/mapper_channel.py` and `cell_diagnostics.py`;
        - the old read-only `rules.db` files;
        - `role_migrate_probe.py`;
        - the `role_*` and `ta_op` mapper modes that no longer exist.
      - **Guard:** `role-cells` = 0.
      - **Verify:** "undo that task" restores the box files; account
        deletion removes the box and nothing of another owner's.
      - **Net:** about −4.1k.
- [ ] 1.7 **P7 Platform tables into `storage/` (S10).** Depends on P2.
      - **Builds:** the `daemon_server.py` tables move into `storage/`
        stores (folds `platform-state-outside-command-centers`).
      - **Deletes:** the duplicate schema helpers.
      - **Guard:** `test_storage_registry_complete`.
      - **Net:** ≤ 0.
- [ ] 1.8 **P8 Off-region backup and the DR drill (S27).** **Floor:** data
      loss. Depends on P6.
      - **Founder spend:** the off-region bucket (about $0-5 a month), as
        a `docs/host-actions.md` row. It blocks only this task.
      - **Builds:** box snapshots and platform state shipped off-region on
        the one snapshot path, plus a weekly drill.
      - **Deletes:** the superseded `deploy/backup.sh` and
        `backup-restore.sh` paths.
      - **Net:** ≤ 0.
- [ ] 1.9 **P9 Close.**
      - Run the full acceptance in design §12: the G1 done check, the
        policy check and the cross-user check.
      - Mark S8, S10, S27 and N10 converged in the registry.
      - Delete `openspec/changes/target-architecture/` and
        `openspec/changes/box-provider-foundation/`.
      - Move any open task from `agent-access-controls` into this spec.
      - Sync the spec deltas and archive this change.
