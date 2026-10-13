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
- [x] 0.3 ADR-022: gVisor is the one backend; P2 is the single cutover
      (Direction 5); the production facts are P2a gates.

## 1. Pull requests (in order)

- [x] 1.1 **P1 Owner policy layer** (#4630). **Floor:** authority and money.
      Built `tinyassets/broker/policy.py`, `/app/api/policy`, broker
      enforcement, the read-only projection, `ta policy show|propose` and
      the guard `policy-writers`. Deleted `agent_rules.py`,
      `approval_scopes.py`, `broker/http_policy.py` and the rule routes.
      Known residual: the daemon-writable owner-session table (ADR-019),
      closed in P3.
- [ ] 1.2 **P2a Preflight on the production host.** Not floor: it serves
      nothing. Depends on P1.
      - **Builds:**
        - the deploy workflow's SSH step installs the pinned `runsc` and
          the `boxhostd` systemd unit on the droplet, `--network=none`,
          cgroup v2 per box; both serve nothing until P2b;
        - `scripts/box_preflight.py`: the host and platform gates G1, G3,
          G4 and G6 of ADR-022 on raw `runsc` sandboxes, pass or fail
          numbers, run in a quiet window. It needs nothing P2b builds.
      - **Deletes:** `.github/workflows/box-kvm-validation.yml` and
        `scripts/box_bench/` (one measurement path).
      - **Verify:** every gate number recorded in the P2b PR description.
        A failed G3 either raises a spending decision to the founder (a
        larger host) or a threshold change recorded and approved in the
        P2b PR with a rerun; no threshold moves silently.
      - **Net:** ≤ 0.
- [ ] 1.3 **P2b The cutover.** **Floor:** cross-user, data, credential
      custody (CLI token lease), exfiltration (the relay). Depends on P2a
      with every gate passed. **This PR holds the one maintenance window.**
      After it, every owner-space execution runs in its owner's box and no
      cell or mapper remains to fall back to (Direction 5). The cross-user
      floor completes at P3 (ADR-022 names the two residuals and their
      bounds); S8 reads converged at P3.
      - **Builds:**
        - the gVisor driver behind `BoxProvider`, re-keyed per owner
          (`bind(owner)`); `boxd`, moved from the `boxes/local.py` core;
          the read-only harness rootfs with Node, the CLI packages'
          install trees, Chromium and the media tools; per-owner project
          quota;
        - `admit` once at session entry; every execution of the session is
          an exec on the one handle;
        - in the box: `read`, `write`, `edit`, `bash`, git, code nodes,
          decoding, media, CLI packages (the file-OAuth token leased for
          one run, ADR-012 D5; the broker in-box endpoint arrives in P3),
          Chromium for preview;
        - one per-box egress relay at the broker under the platform floor
          only (owner policy modes arrive in P5); git over HTTPS uses it
          with `broker/git_http` credentials;
        - `BoxProvider.destroy` under the owner-wide deletion fence;
        - lifecycle: a box is stopped after `idle_s` and cold-started on
          the next exec; files persist; checkpoint and restore arrive in
          P6;
        - the daemon container starts unprivileged: the `seccomp`,
          `apparmor` and `systempaths` unconfined lines leave
          `deploy/compose.yml` and the droplet's copy in the window.
      - **Window runbook:** the design §11 steps, with a verified full
        backup first.
      - **Deletes:**
        - all 33 `role_*` modules (design §8), the mapper
          (`deploy/role_owner_launcher.py`), `deploy/role_launcher.py`,
          `role_provision.py`, `role_migrate.py` (the one-shot cutover is
          written for this window and removed by P9);
        - `node_sandbox.py`, the `universe_tools.py` jail path,
          `jail_disk.py`, `sandbox/`, `providers/provider_jail.py`,
          `browser_cell.py`, `browser_egress.py`, `universe_egress.py`,
          `git_egress.py`, `owner_launcher_client.py`, `owner_socket.py`,
          `broker/mapper_channel.py`, `cell_diagnostics.py`;
        - the eight `scripts/role_*.py` probes, replaced by
          `scripts/box_acceptance.py`;
        - the old read-only `rules.db` files.
      - **Guard:** `role-cells` = 0; `test_universe_path_io_guard` = 0.
      - **Acceptance gates, before the window:** G2 (cross-user, handle
        auth) and G5 (`usage`, daemon-restart journaling) of ADR-022 pass
        on this build in `scripts/linux_oracle.py` and on a production
        copy, with a recorded pass in the PR.
      - **Verify:** a production-copy turn runs real tools, a CLI package
        and a preview in the box.
      - **Net:** about −9.5k.
- [ ] 1.4 **P3 Loop in the box.** **Floor:** credential custody. Depends
      on P2b, C3 (`one-model-call-path`) and C8 (`session.start`).
      The same `boxd` operations, run by a loop process inside the box
      instead of the daemon. The cross-user floor is complete here and S8
      reads converged here.
      - **Builds:**
        - `session.start` runs the loop as a process in the owner's box;
          model calls leave through the broker gate (`model_call`);
          child sessions inherit the parent's handle and allocation;
          resident processes run in the box (folds
          `resident-agent-processes`);
        - the broker's in-box endpoint; CLI packages switch to
          `auth.mode: broker_endpoint` and the one-run token lease ends;
        - owner-session minting and validation in broker custody (the P1
          residual closes);
        - a session survives app close and daemon deploys.
      - **Deletes:** the daemon-side loop placement (`_call_writer` path);
        folds the open tasks of `remote-box-ta-bridge` and
        `remote-box-extension-runtime`.
      - **Verify:** the G1 done check in design §12, live.
      - **Net:** ≤ 0.
- [ ] 1.5 **P4 The N4 view hook.** **Floor:** cross-user view. Depends on
      P2b.
      - **Builds:** `BoxProvider.view` relayed by `boxhostd`, opened only
        by the owner door with the owner's session.
      - **Deletes:** `role_image_oracle.py`.
      - **Verify:** a non-owner session cannot open the view; no typed
        secret appears in a transcript or a receipt.
      - **Net:** ≤ 0.
- [ ] 1.6 **P5 Owner egress policy at the relay (the N5 point).**
      **Floor:** exfiltration. Depends on P1 and P2b.
      - **Builds:** the owner egress modes (`open`, `allowlist`, `ask`)
        and connection grants on the one per-box relay.
      - **Deletes:** `role_http_fixture.py`.
      - **Verify:** an unlisted host is denied under `allowlist`; `ask`
        holds the connection and raises an owner request; SMTP is refused.
      - **Net:** ≤ 0.
- [ ] 1.7 **P6 Suspend, restore and the snapshot hook.** **Floor:** data
      loss. Depends on P3, P4 and P5.
      - **Builds:**
        - checkpoint on idle and restore on wake replace stop and start;
          checkpoint across a `boxhostd` restart, falling back to a cold
          boot plus the journal; the real checkpoint size and time are
          measured here with the loop and the browser inside;
        - `BoxProvider.snapshot` and `restore`, with one automatic
          snapshot per session.
      - **Deletes:** the stop-and-start lifecycle branch.
      - **Verify:** "undo that task" restores the box files; a `boxhostd`
        restart resumes every awake session.
      - **Net:** ≤ 0.
- [ ] 1.8 **P7 Platform tables into `storage/` (S10).** Depends on P2b.
      - **Builds:** the `daemon_server.py` tables move into `storage/`
        stores (folds `platform-state-outside-command-centers`).
      - **Deletes:** the duplicate schema helpers.
      - **Guard:** `test_storage_registry_complete`.
      - **Net:** ≤ 0.
- [ ] 1.9 **P8 Off-region backup and the DR drill (S27).** **Floor:** data
      loss. Depends on P6.
      - **Founder spend:** the off-region bucket (about $0-5 a month), as
        a `docs/host-actions.md` row. It blocks only this task.
      - **Builds:** box snapshots and platform state shipped off-region on
        the one snapshot path, plus a weekly drill.
      - **Deletes:** the superseded `deploy/backup.sh` and
        `backup-restore.sh` paths.
      - **Net:** ≤ 0.
- [ ] 1.10 **P9 Close.**
      - Run the full acceptance in design §12: the G1 done check, the
        policy check and the cross-user check.
      - Mark S8, S10, S27 and N10 converged in the registry.
      - Delete `openspec/changes/target-architecture/`,
        `openspec/changes/box-provider-foundation/` and the one-shot
        cutover.
      - Move any open task from `agent-access-controls` into this spec.
      - Sync the spec deltas and archive this change.
