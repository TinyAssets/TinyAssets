## Status (2026-10-06)

Nothing in this change runs in production. The build lives on two branches:

- **U1** `feat/per-role-uid-split` at `c8c5042654` (#4523): image, broker, owner identities,
  bounded mapper, bootstrap and the per-class owner cells, D1-D87.
- **U2** `feat/per-role-uid-split-migration` at `58395c4d99` (#4510): contains U1, plus the
  volume migration D200-D217, two-pass delete D218, and the switched startup overlay (default
  OFF).

Build evidence: `delivery.md` at `c8c5042654` (U1) and `delivery-u2.md` at `58395c4d99` (U2).
Neither delivery file lands on main. A box below is checked only when it is built AND proven,
and the proof is named. "Proven" means a probe on a production-Dockerfile image or the root
Linux oracle on the branch. Nothing is proven on main until its landing slice (section 3)
merges. Two OFF switches keep all of it inert: `TINYASSETS_CREDENTIAL_BROKER=process`, set
nowhere in `deploy/`, and the bounded mapper client, installed only by the D70 bootstrap.
Startup refuses at `deploy/role_launcher.py:435`.

**D60 supersedes the shared engine uid 1003.** Each owner has a dedicated UID/GID
(300001..399999, D61/D62). Text below that still says 1003 refers to the vestigial image user.

## 1. Design (this change)

- [x] 1.1 Proposal, design and spec delta; uid map agreed with agent-loop and openshell-spike.
- [x] 1.2 Cross-family security refute, two rounds, folded as D2/D4/D6/D7; D8 then D9 widened
      isolation to every engine class; founder D60 replaced the shared engine identity.
- [ ] 1.3 **Founder/spec: dynamic admission and the admission-generation contract.** Centers and
      users created after startup must be admitted to the mapper and labelled `1001:<owner>` with
      no capabilities. D216 must accept a grown or shrunk principal set. Implemented on U1
      (owner-dynamic-admission, spec #4541) and wired into U2's startup coordinator (D221).
      Open: the spec landing.
- [ ] 1.4 New D-record for the startup overlay: PID1 without tini, health retiring to 1001 before
      `ta-op pulse`, and `CAP_SYS_ADMIN` dropped from `MASK 0x2001c1` (`deploy/native/ta_op.c:82`).
      This supersedes 2.7's original `MASK` plan.

## 2. Build, against the original tasks

- [ ] 2.1 **Image: PARTIAL.**
  - [x] Users 1002/1003, groups 1100-1102, `venv --copies`, entry moved to
        `/usr/local/libexec`, `/app` read-only, `HOME=/home/tinyassets`, `ta-chain.py`
        (D14, `02d5542a78`). `check_privileged_chain.py` passes on every local image build.
  - [ ] `/app` runtime-write audit: `codex_provider._codex_workdir` defaults to the source root
        (delivery.md L5267 at `c8c5042654`). Blocks L4.
  - [ ] CMD switch: U2 overlay `deploy/compose.role-split.yml` committed at `58395c4d99`, not
        landed.
  - [ ] Remove or document uid 1003, which D60 leaves vestigial.
- [ ] 2.2 **Launcher, reshaped by D60/D62/D68-D70.**
  - [x] Owner identity reservations D61 (`4cd932f044`), fenced identity IPC and the bounded
        namespace map `0 300000 100000` D62 (`39a1ce6887`), descriptor label enforcement D65
        (`4e112da74c`).
  - [x] Bounded mapper with decoder cells D68 (`cfc766bf2d`), `SCM_CREDENTIALS` daemon client
        D69 (`b4b430f727`), bootstrap with PID1 retiring to 1001 D70 (`5b020514d9`), named
        seccomp profiles D52 (`69ee880edc`), cell lifetimes D76/D77 (`eeeeb0ff49`,
        `d1f84c63e5`).
  - [x] Owner delete pass one D85 (`9657e679b7`).
  - [x] Two-pass delete D218 (U2 `c540ae5e20`), retiring before finish (D221). Production
        image: `role_admission_startup_probe.py` deletes through the real mapper, including a
        crash before retire and a center on `missing`.
  - [ ] Engine-MCP environment-consumer audit.
  - [ ] Pool removal and `scoped_reset` still traverse as the daemon. Needs a subtree
        owner-delete cell (lane B).
- [ ] 2.3 **Chain verification: done in code, never run in CI.** Draft PRs skip `build-smoke`, so
      it first runs when L4 is non-draft.
- [ ] 2.4 **Volume migration: PARTIAL (U2).**
  - [x] Egress relocation and rollback D11/D12/D15/D67 (`02d5542a78`, `fedd717970`).
  - [x] Owner and metadata migration D200-D217: journal and quarantine (`34a85c9e35`),
        inode-generation provenance D214/D215/D217 (`ab3553a08d`, `d5cd06acf1`,
        `ae51a73896`), reconcile D216 (`6a1c2a0fdf`, `3fb4b00ab2`). Root oracle 161 tests x3,
        9 crash boundaries. Production alias scan: 0 cross-owner inodes.
  - [x] Rollback probe `old_cmd_boot=true` with the unchanged old-image CMD (`6e6b74b8fb`), using
        the dev auth fixture.
  - [x] A changed principal set is reconciled by the admission contract (D221), not refused:
        `tests/test_role_admission_startup.py` (root oracle) and
        `scripts/role_admission_startup_probe.py` (real startup boot, reverse mode, old image).
  - [ ] First-volume identity-map initialization (D61 says only it may create the map; not wired).
  - [ ] Production ext4/ACL check.
  - [ ] Rollback probe under production auth.
  - [ ] The operator CLI `rollback.md` describes.
- [ ] 2.5 **Every owner-scoped spawn through an owner cell: PARTIAL.**
  - [x] Accepted classes, each with zero foreign bytes on a production-Dockerfile image: decoder
        D68/D69, workspace git D71 (`340318fe4d`), git_bridge D72 (`93c6dd98b2`), preview
        D73/D75 (`91c244c079`), node D78 (`ff6506c757`), tool jail D79/D80/D83 (`220f612a81`,
        `f65af2de53`, `2f07b72490`), video D81 (`d3f99e9134`), provider discovery D82
        (`1a095dfc40`), packages D84/D87 (`35df4b4ecd`, `c8c5042654`), owner delete D85
        (`9657e679b7`), provider exec text-only D86 (`4aad725f28`).
  - [ ] No shipped provider adapter reaches provider exec: Codex `universe_view` and Claude `cwd`
        both refuse (lane C1).
  - [ ] Session persistence; engine-MCP thin proxy (C2); network metadata egress (C4); workspace
        provision/registry/worker, remote git, local box (C3); other ingestion formats; the K1
        package consumer (C4).
  - [ ] Full reader matrix. Concern `2026-10-05-role-reader-hardlink-alias.md` stays open.
  - [ ] D87 RSS/CPU under pressure (lane E).
- [ ] 2.6 **`start_broker`, fence, legacy path: MOSTLY.** D16/D18 lifecycle and acquisition,
      D19-D53 consumer routing (see `broker-access-inventory.md`). Remaining broker readers need
      dynamic admission (lane D).
- [ ] 2.7 **Entrypoint, compose, capability set: NOT landed.** The U2 overlay (`58395c4d99`) uses
      `user 0:0`, 7 caps and no tini, and leaves the `ta_op.c` `MASK` unchanged. Needs 1.4.
- [ ] 2.8 **Production-image oracle: PARTIAL.** Per-class probes above pass. Still missing: an
      integrated startup-ON run, aggregate memory/tmpfs, `ta-op pulse` under the split, and the
      D8/D9/D60 acceptance matrix in `design.md` (every class, every writable-path/reader pair,
      migration, two-pass delete, old-image rollback, healthcheck). No skip counts as a pass.
- [ ] 2.9 Prod verification: per-role and per-owner uids in `ps`, one owner stream served, RSS
      per stream measured, `deployed_sha.py --assert-contains`, `mcp_public_canary.py
      --assert-handles`, one real-user app pass.
- [ ] 2.10 Spec sync and archive.

## 3. Landing slices

Each slice is one PR to main with the switch OFF and the plugin mirror regenerated. Files come
from the final branch state (`git checkout origin/feat/per-role-uid-split -- <files>`). Neither
branch is rewritten. WIP `b94259d502` never lands. Both branches are kept until every slice
lands. Pushes touching `Dockerfile`, `deploy/` or `tinyassets/` deploy production. Release-
critical slices need `infra-change`, an APPROVE receipt per head, at most 8 sensitive files, and
must be non-draft so `build-smoke` runs.

| Slice | Contents | Depends on | Release-critical | Proof |
|---|---|---|---|---|
| L0 | This spec: proposal (D60), design/rollback from U2, truthful tasks, specs, broker access inventory, 3 concerns | — | 0 | `openspec validate --strict`, flow audit, structural guards |
| L1 | `role_modes`, `workspace_fs`, `universe_files`, `process_liveness` + reader/liveness tests. Ungated | L0 | 0 | Production `nlink>1` measurement first (R2); turn-runner reclaim tests (R3) |
| L2 | `tinyassets/broker/*`, `storage/outbound_connections.py`, timer inventory + broker tests | L1 | 0 | — |
| L3 | Consumer rewiring under `broker_selected()` (api, effectors, onboarding, providers, budgets, intents, automations, account deletion D53, owner stores, accounting) | L2 | 0 | Every new branch gated; full tests on the unselected path |
| L4 | Image: Dockerfile (no ffmpeg/acl/migration COPYs), compose HOME, docker-build.yml, broker_main, role_launcher, check_privileged_chain. **Changes production** | L2, `/app` audit | 5 | Non-draft build-smoke; legacy-CMD boot with `ta-op pulse`, canary, a real Codex turn; post-deploy `deployed_sha`, canary `--assert-handles`, app pass; revert by retag |
| L5 | Owner launcher and decoder (all class admission, inert), role_git, client, image bytes, snapshots, vault, seccomp; oracle production-image mode; the class probes | L4 | 4 | The probes on the merged image |
| L6 | git, bridge, preview, node classes | L5 | 0 | — |
| L7 | Tool classes, relays, egress, provider jail | L5 | 0 (1 if acl) | — |
| L8 | Video and provider cells, ingestion. Hold ffmpeg out (R4) | L5 | 0 | — |
| L9 | Packages and owner delete pass one | L5 | 0 | — |
| L10 | Egress migration, `backup.sh`, runbook | L4 | 3 | dr-drill restore of the new archive format |
| L11 | U2 migration, rebased on main | L10, L5 | 6 | Root oracle 161x3; rollback probe |
| L12 | U2 two-pass delete D218 | L9, L11 | 1 | Production-image delete probe; legacy `.layout.json` path |

- [x] 3.0 L0, this spec.
- [ ] 3.1 L1
- [ ] 3.2 L2
- [ ] 3.3 L3
- [ ] 3.4 L4
- [ ] 3.5 L5
- [ ] 3.6 L6
- [ ] 3.7 L7
- [ ] 3.8 L8
- [ ] 3.9 L9
- [ ] 3.10 L10
- [ ] 3.11 L11
- [ ] 3.12 L12

L6-L10 can merge in any order after L5. Risks while OFF: R1 `/app` read-only and the HOME move
(L4); R2 `workspace_fs` refuses `st_nlink != 1` ungated, and production holds multi-link files
(52,167 names against 52,162 sole-owner inodes), so measure or gate it (L1); R3 the read-only
`owner_state` flock drives `agent_turn_runner` reclaim, where UNKNOWN instead of DEAD means stuck
turns (L1); R4 ffmpeg runs the legacy `video_extractor` unconfined (L8); R5 backup format (L10);
R6 the `http_connection` refactor and `_HTTP_ACTION_CAP` removal (L3); R7 deletion layout
classification (L12).

## 4. Activation lanes (after landing)

- [ ] 4.D Dynamic admissions: mapper binding extension over an authenticated daemon request,
      capability-free center labelling. Needs 1.3. 5-7 days.
- [ ] 4.A Startup and health (after L11/L12): PID1 without tini, first-volume identity-map
      initialization, health retiring before `ta-op pulse`, `SYS_ADMIN` out of `MASK`, a gated
      workflow that applies the overlay. Needs 1.4. 4-6 days.
- [ ] 4.B Migration, deletion and rollback: admission-generation fix, subtree owner-delete cell,
      production ext4/ACL, the rollback CLI, production D218 probe. 5-7 days.
- [ ] 4.C Providers through provider exec: C1 persistent workspace and session view plus Codex
      and Claude adapters; C2 engine-MCP thin proxy; C3 workspace provision/registry/worker,
      remote git, local box; C4 network discovery egress and the K1 package consumer.
      18-24 days.
- [ ] 4.E Aggregate memory and tmpfs: global RSS/tmpfs budget, `oom_score_adj`, a 32-cell
      pressure probe, RSS per stream. 3-5 days.
- [ ] 4.F Integrated production proof: overlay ON over synthetic and restored-backup clones,
      every class, the reader matrix, migration, health, reverse and the old image. 4-6 days.
- [ ] 4.G Deploy and rollback procedure. 2-3 days.

Critical path: L0, L1, L2, L4 (after the `/app` audit), L5, L8, C1, C2, F, G. The 1.3 spec runs
in parallel with L1-L4.
