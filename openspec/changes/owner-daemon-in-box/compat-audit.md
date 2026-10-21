# C6 migration-compromise audit (read-only)

Worktree `C:\Users\Jonathan\Projects\wf-owner-daemon-s3`, branch `conv/c6-owner-daemon-s3`,
HEAD `47118dd80e7`, merge-base with origin/main `417f6a4c003`. Slices 1-3 are built (owner-daemon-in-box
tasks.md:13-28); slices 4-7 are not. The rule applied: a path that keeps an old shape, old data or an old
provider alive at runtime is a finding. A one-shot step that runs inside the gated cutover and is then
deleted is acceptable.

## Headline

1. **About 30 `owner_runtime.current() is None` forks keep the shared-daemon path alive beside owner mode (B).**
   No task row deletes them. Design D7 (design.md:132-138) lists the deletions, but tasks 4-7
   (tasks.md:29-38) are lifecycle, waker, migration, deploy roll and cutover, and none of them says
   "delete the `current() is None` branches, `control_plane/binding.py`, `box_tools`/`BoxExecutor` and the
   per-exec box provider". Tasks.md:6 says only "slice 1 retains shared paths for slice 2".
2. **The CLI providers contradict founder decision D3 (A).** Design.md:83 says the in-box CLI route "is
   deleted, not left refusing". The code still refuses: `external_agents/provider.py:28-32`,
   `providers/owned_process.py:142-146`, `subscription_refresh.py:535-540`,
   `providers/served_model_plan.py:82-85`. The whole CLI stack also stays live on the non-owner path,
   and the design text still contradicts itself (design.md:35, "Provider CLIs run in the box, as on main").
3. **New shared stores are read-only stubs while the old writers stay (B).** `.broker/plans.db`,
   `.published/` and `.hook-owners.sqlite3` have no production writer:
   - `Plans.apply`, `HookOwners.publish` and `HookOwners.revoke` have no callers (grep);
   - Stripe still writes the per-center `.subscription_state.db` (`billing/plan_action.py:399,468`);
   - commons still reads and writes the shared `.tinyassets.db` (`commons_catalog.py:943-1000`).

   main-audit.md:23-26 acknowledges this.
4. **The P2b one-shot migration carries an intermediate shape (A/C, part inference).** `box_layout_migration`
   moves owner-split to the per-exec owner-box layout. It copies CLI credentials into host platform custody
   (`.platform/credential-vaults/<center>`, :434-453, :569-583) and copies CLI agent homes into the box
   (:588-607), and it has a second activity-conversion pass for volumes already on owner-box (:538-556,
   :280-309).
   - Production runs `fa29c95` (main-audit.md:250). Inference: production is still owner-split, so the
     already-owner-box branch is never needed.
   - Under D3 (users reconnect), the credential and agent-home copies only carry data that the final shape
     refuses to read: `credential_vault.py:160-163` refuses in owner mode.
5. **Pre-C6 startup migrations and legacy readers still run at runtime (A, not C6-introduced):**
   - the consent move runs on every router start through `require_layout` (`universe_server.py:4489-4491`
     → `storage_layout.py:242-246` → `platform_state_move.run`);
   - the legacy `PROGRAM.md` premise mirror (`universe_soul.py:3-4,18,101,322`);
   - the legacy-launcher branch in `deploy/box_daemon_entry.py:13-22,29-38`;
   - the dead `boxes/lifecycle.owner_layout`, which has no callers (`boxes/lifecycle.py:8-16`).

---

## (A) Runtime compatibility kept for migration

| # | file:line | What it preserves | Delete at cutover; user cost |
|---|---|---|---|
| A1 | `external_agents/provider.py:28-32`; `providers/owned_process.py:142-146` | CLI provider classes remain and refuse in owner mode "until broker_endpoint auth is implemented", which contradicts D3 (design.md:83) | Delete the CLI provider route. Cost: CLI-connected users reconnect a model source through the broker; the cutover report names them |
| A2 | `providers/provider_resolver.py:17,39-45`; `providers/claude_provider.py`; `external_agents/*` (incl. `packages/codex`); `providers/native_discovery.py:100-125,194-205`; `control_plane/native_launch.py:5-24`; `boxes/cli_launcher.py`, `boxes/cli_wire.py` | The whole CLI model path stays registered and runnable on the non-owner (shared-daemon) path | Same as A1. All of it goes |
| A3 | `subscription_refresh.py:535-540`; `credential_refresh.py`; `providers/served_model_plan.py:82-85` | CLI subscription refresh and source listing. Owner mode returns early, with the comment "CLI sources have no broker_endpoint executor yet" | Delete. Cost: reconnect |
| A4 | `credential_vault.py:158-166` (custody root `.platform/credential-vaults/<center>`); CLI overlay `:2372-2450` ("Deprecated compatibility", "remaining legacy CLI executor's owner credential overlay"); `api/llm_deposit.py:33,124-183`; MCP doc for `connect_llm` deposit `universe_server.py:1298-1302` | Host-side raw credential custody and the public MCP action that deposits Claude/Codex CLI auth. Owner mode refuses vault access, but the surface and store stay | Delete the vault CLI records, the deposit action and the host custody dir. Cost: users reconnect through the broker connection flow |
| A5 | `box_layout_migration.py:434-453, 569-583` (`_copy_credentials` into `.platform/credential-vaults`) | The one-shot carries old credential files into host custody that the final shape refuses to read (`credential_vault.py:160-163`) | Drop it from the migration. Cost: reconnect (accepted by D3) |
| A6 | `box_layout_migration.py:588-607` (`.platform/owners/<uid>/agent-home` → `<box>/.agent-home`, excluding package auth files) | CLI package homes (state for retired CLIs) are carried into the box | Drop it. Cost: CLI state is lost (CLIs are retired) |
| A7 | `boxes/lifecycle.py:8-16` `owner_layout()` | Treats a missing marker as `owner-split`; has no callers (grep) | Delete. No user cost |
| A8 | `deploy/box_daemon_entry.py:13-22, 29-38` | Selects `/usr/local/libexec/ta-launch.py` (the legacy launcher) for owner-split or marker-less data; the image no longer ships it (Dockerfile:361) | Delete the branch and the whole shared-daemon entry with the per-exec frontend (see B21). No user cost |
| A9 | `storage_layout.py:180, 194-196, 215-256`; `storage/platform_state_move.py` (whole); `storage/effector_consents.py:44-51` `legacy_consents_db_path` | The 2026-10-03 consent move still runs at every router start through `require_layout()` (`universe_server.py:4489-4491`). `box_layout` absent means owner-split | Delete the move and the legacy path. The router root has no centers after cutover. No user cost (consents were already re-asked) |
| A10 | `.effector_consents.db.premigration` copies in centers (`platform_state_move.py:37,106-120`) are not excluded by `box_layout_migration._copy` (`:35-37,402-409`); `storage_accounting.py:249-251` charges them | Old renamed consent DBs are copied into each owner box | Exclude or delete them at cutover. No user cost |
| A11 | `universe_soul.py:3-4,18,101-102,322-331`; callers `api/universe.py:356,1149,1905,3517,3583,4540`; `evaluation/structural.py:912-915` | The `PROGRAM.md` premise is kept as a "compatibility mirror" for reads and writes. Pre-C6, but it is a legacy reader inside the box | Delete it. Convert `PROGRAM.md` → `soul.md` once at cutover, or drop it. Cost: none if converted once |
| A12 | `api/first_contact.py:47-72` | `principal_is_deleted` fails open on a missing table "so a pre-migration database still serves" | Delete the fail-open; the cutover creates the table. Pre-C6 |
| A13 | `account_deletion.py:1024-1038` | After box destroy, refuses when "legacy owner bytes remain" in the host tree, which the P2b move leaves in place (`box_layout_migration.py:3`) | Delete it once the cutover removes the host trees (D6 step 4). No user cost |
| A14 | `providers/router.py:1557-1560` "Preserve legacy host routing" (auth-invalid cooldown) | The shared-host credential-cooldown semantics. *Inference:* moot with one owner per process | Review at cutover. Pre-C6 |

## (B) Dual paths during staging (shared-daemon path beside owner mode)

These must all go in the cutover slice. Each line is the fork; the `else` / `current() is None` side is the old path.

| # | file:line | Old path kept |
|---|---|---|
| B1 | `control_plane/binding.py` whole module (file_binding `:148-170`, `bind_session`, `_ACTIVE`) | Session-bound box handles; owner mode returns `None` at `:152-153` |
| B2 | `control_plane/session.py:143-182` | `_binding` mints a box binding through `configured_box_provider` |
| B3 | `universe_files.py:146-151, 197-206, 371-391, 615-619, 694-703` | Owner-file I/O through `file_binding` → `BoxProvider`; owner fork at `:695-703` |
| B4 | `universe_tools.py:92-104, 115-121, 132-139, 215-217, 236` | `BoxExecutor` exec bridge vs local `OwnerTools` |
| B5 | `agent_loop/tool_session.py:113-123` | Box-bridge `bind_box` vs local tools |
| B6 | `agent_loop/box_tools.py` (662 lines), `agent_loop/served_chat.py:40-80` (`configured_box_provider`, `BoxExecutor`) | The four-tool exec bridge (`start_exec`) that D7 lists for deletion |
| B7 | `providers/call.py:53-59` | `ServedChatAgentAdapter` (daemon-side loop) when neither owner mode nor a binding applies |
| B8 | `engine_mcp_server.py:4786-4793` | `active_binding` lookup; error when there is neither binding nor owner mode |
| B9 | `engine_mcp_http.py:284-291, 424-427` | Host engine socket dir and `_protect_daemon()` outside owner mode. The router no longer starts engine endpoints, so this is reachable only on the old path |
| B10 | `broker/client.py:100-114` plus `_verify_peer` checks at `:131,161,189,221,253,288,324,390`; refresh factory `:359` | Host broker socket and daemon-side credential refresh vs the box channel. *Note:* the router keeps a broker client for `OWNER_BOX_ROUTE`; the owner-data ops on the host client are the B part |
| B11 | `broker/aclient.py:122-130, 195-199` | Same, for async: host socket and daemon-side refresh |
| B12 | `broker/supervisor.py:159-167` | Host supervisor registry vs `OwnerChannel` adapter |
| B13 | `broker/owner_identities.py:325-335` | `ADMITTED_OWNER` over the host owner socket vs `BOX_IDENTITY` |
| B14 | `broker/server.py` `self._role != OWNER` / `elif self._role != OWNER` branches (diff) | The broker still serves every owner-data op to the shared daemon (OWNER role) beside the new BOX role. After cutover the OWNER role should keep router ops only (*inference* about the intended end state) |
| B15 | `commons_catalog.py:933-940, 966-974` | Commons read from the shared `.tinyassets.db` (base_path) vs `.published/` through the broker. `.published/` has no writer |
| B16 | `storage/subscription_state.py:57-60, 95-98, 120-123, 149-154`; `universe_owner.py:193-198` | Per-center `.subscription_state.db` (still the Stripe writer's target, `billing/plan_action.py:399,468`) vs `.broker/plans.db` (no writer; `Plans.apply` is uncalled). `Plans.read` returns `free` when the db or row is missing (`broker/owner_reads.py:16-20`), so a missed seed would silently downgrade paid users |
| B17 | `router_credentials.py:14-26` | `HookOwners.publish/revoke` are uncalled. Hook creation and revocation still write only the old in-box rows (`storage/webhook_hooks.py`); the router map would go stale (*inference*: the publication seam is unbuilt; main-audit.md:28-29) |
| B18 | `storage_accounting.py:145-149` vs `:170-200` | Local walk vs box-provider measurement session |
| B19 | `providers/base.py:1055-1064` | Sandbox status from `configured_box_provider` |
| B20 | `storage/agent_request_usage.py:252-268`; `providers/model_call.py:106-111`; `providers/api_key_http_provider.py:262-267` | Host liveness lease, host model path and no source publication outside owner mode |
| B21 | `boxes/frontend.py`, `boxes/production_files.py`, `boxes/file_worker.py`, `boxes/exec_worker.py`, `boxes/executions.py` (per-owner FIFO, whole-box stop), `boxes/gvisor_files.py:70-100` (awake cap 2), `deploy/box_daemon_entry.py:39-42` | The P2b per-exec box backend that serves the shared daemon. Slice 4 replaces it with a persistent per-owner sandbox; no task names this deletion |
| B22 | Shared-daemon-only box callers with no owner-mode port: `workspace_worker.py:199-217`, `workspace_fs.py:1036-1066`, `wake_conditions.py:111-139`, `boxes/lifecycle.py:19-34` (`admit_center`, called from `api/universe.py:4525-4527`), `account_deletion.py:1004-1023` | These bind through `configured_box_provider`, which is `None` in owner mode. *Inference:* center creation, workspace sweep, wake probes and account destroy fail in owner mode until they are ported or moved to the router/boxhostd. main-audit.md:230-246 lists some of them |
| B23 | `api_keys.py:258-282` ("direct unit consumers retain that admission here as well"); `auth/middleware.py:1210, 1216, 1262` (`trusted is None` paths) | In-process public-credential authentication beside router-forwarded identity. The `trusted is None and not router` branch is the old full-serving daemon |
| B24 | `universe_server.py:4161, 4198-4202, 4317, 4388-4389, 4405-4406, 4421-4426` | One app factory forks router vs owner. *Ambiguous:* design D1 keeps "the same image and code", so the fork itself is the role split (D). But each `not owner_mode` arm is the only thing keeping the router's process from serving owner routes, and nothing else enforces it |
| B25 | `onboarding/owner_sessions.py:99-107` | Box uses router metadata; otherwise local cookie lookup. Router-side lookup is D (identity); a box-side local lookup would be B |

**Deletion task:** none explicit. D7 (design.md:136-138) names "`box_tools` exec bridge; `file_binding` owner
routing; daemon-side `_call_writer` placement; cross-owner listing filters; multi-owner tick loop". No
tasks.md row (4-7) carries those deletions, and B9-B25 are not named anywhere. Recommend a task row in slice
7 (or 6b): delete every `owner_runtime.current()` fork with its `None` arm, `control_plane/binding.py`,
`box_tools`, the per-exec `boxes/*` provider, the CLI stack (A1-A4), and the OWNER-role owner-data broker ops.
Add a guard: `current() is None` must not appear outside the router entry.

## (C) One-shot migration code

| # | file:line | Status | Deletion task |
|---|---|---|---|
| C1 | `box_layout_migration.py` (whole; `apply` `:529-632`) | One-shot, gated by paired backup and the layout lock. The old trees stay in place (`:3`) | one-box-per-owner tasks 1.10 "delete ... the one-shot cutover" (tasks.md:229-231) and design §8 row 33 "deleted in P9". It is not tied to the owner-daemon slice 6 migration that supersedes it |
| C2 | `box_layout_migration.py:168-188, 473-479, 538-556, 280-309` | Second "activity cutover" pass for an already-owner-box volume | Same P9 row. *Inference:* dead if production is still owner-split; fold into one migration |
| C3 | `activity_shape_migration.py` (whole) | C10 one-shot, called only from C1 (main-audit.md:374-378) | No explicit row (*inference*: covered by P9 "one-shot cutover") |
| C4 | `agent_file_migration.py` (whole); `deploy/deploy_fail_safe.sh:1680-1731` | C8 one-shot, still wired into every deploy (check exits 0 once migrated). Docstring cites deleted `role_content.publish_as_root` (`:36`) | **No deletion task found** |
| C5 | `deploy/deploy_fail_safe.sh:372-408, 1633-1644, 1955-2080` (`owner-split` candidate arm; `getattr(..., "owner-split")` defaults) | Lets older images and rollback cross the layout gate | Delete with C1 after cutover (P9). No explicit row |
| C6 | Slice 6 migration (design D6, D8): split shared DBs, seed `plans.db`, `.published/`, hook digests (design.md:72,84) | Not built | tasks.md:33-35 has no "delete after cutover" clause |
| C7 | `storage/platform_state_move.py` | Was a one-shot; now a standing startup step (A9) | None |

## (D) Fine (ambiguous ones only)

- `owner_channel.py:29-35`: `OwnerChannel.fence()` returns "compatibility fields" so existing broker
  callers accept it as a supervisor. This is an adapter inside the new path, not old data; it could become a
  first-class interface when the B10-B12 forks go.
- `owner_runtime.py:130-139`: `serving()` is a no-op without owner mode. That is the router role (D), and it
  disappears with B24.
- `platform_runtime_provenance.py:450-453`: synthesizes the owner observation from relay admission.
- `broker/owner_sources.py:17-18`: "owner mode requires an HTTP broker source". This is the one-model-path rule (consistent with D3).
- `tick.py`, `provider_serving_binding.py`, `engine_mcp_http.py` discovery loops enumerate the root DB with no owner filter. That is correct inside a single-owner box (main-audit.md:242-246).

---

## Migration-convenience justifications in docs, decisions and comments

| Where | Quote | Note |
|---|---|---|
| owner-daemon-in-box/design.md:35 | "Provider CLIs run in the box, as on main." | Stale. It contradicts design.md:83 (D3) and should be deleted |
| owner-daemon-in-box/design.md:70 | "Existing protected-owner cookies resolve only through the router session map" | Keeps existing sessions alive across cutover. Acceptable as router identity state |
| owner-daemon-in-box/design.md:72 | "the migration seeds existing digests" (hook map) | One-shot (C). Re-creating hooks would also be an acceptable cost |
| owner-daemon-in-box/design.md:84 | "slice 6 seeds these shared stores at cutover" | One-shot (C). Billing must be seeded; published data could be re-published |
| router_credentials.py:3 | "The cutover seeds digests from existing hooks." | Same as above |
| owner-daemon-in-box/tasks.md:6 | "slice 1 retains shared paths for slice 2" | The origin of B; no later row deletes them |
| owner-daemon-in-box/main-audit.md:61-62 | "The fix reuses the broker's existing admission transaction and preserves daemon-side grant queries" | "preserves daemon-side grant queries" is a kept daemon path |
| owner-daemon-in-box/main-audit.md:26-27 | "Provider CLIs refuse pending broker-endpoint authentication" | Superseded by D3; the code still does this (A1) |
| one-box-per-owner/design.md:380-381 | "Kept: `broker/owner_identities.py`. Its permanent UIDs become the boxes' host UIDs, so no identity is reissued." | Justified by not reissuing identities. The outcome is D (the broker is identity), but the stated reason is migration convenience |
| one-box-per-owner/design.md:44, 53-57 | "Native CLIs as external-agent packages (their model traffic goes to the broker's in-box endpoint)"; "The one exception is a file-OAuth CLI's own token, leased into its owner's box for one run." | CLI-preserving rules superseded by D3. They should be struck |
| one-box-per-owner/tasks.md:171-172 | "CLI packages switch to `auth.mode: broker_endpoint` and the one-run token lease ends" | Same. D3 says no broker endpoint for CLIs |
| one-box-per-owner/design.md:722-723 | "Existing provider factory callsites remain unchanged until C7 supplies this binding." | Keeps the CLI factory path |
| one-box-per-owner/tasks.md:138, 142 | "The old image keeps serving." / "Preserve the current image's runtime behavior." | P2b staging statements (historical) |
| one-box-per-owner/design.md:464-466 | "Only then are the old trees removed" | One-shot sequencing; fine, but no code removes the trees yet (main-audit.md:288) |
| universe_soul.py:3-4 | "keeps the old `PROGRAM.md` premise file as a compatibility mirror" | A11 |
| api/universe.py:1903 | "Premise remains the public compatibility field." | A11 |
| api/first_contact.py:50 | "fail-open on a missing table so a pre-migration database still serves" | A12 |
| storage_layout.py:245 | "Fence volumes already moved by the unfenced PR image." | A9 |
| credential_vault.py:2373, 2379, 2435 | "Deprecated compatibility" (x2); "Apply the remaining legacy CLI executor's owner credential overlay." | A4 |
| providers/router.py:1558 | "Preserve legacy host routing." | A14 |
| owner_channel.py:30 | "The broker replaces these compatibility fields with its current fence." | D (adapter) |

Precedents that already take the user cost (useful for the cutover report):
- one-box-per-owner/design.md:211: "Legacy `rules.db` rules are not migrated ... owners re-grant".
- platform_state_move.py:12-16: "Nothing is carried forward ... Every consent is granted again".

## Recommended cutover-slice changes (summary)

1. Delete the CLI provider stack (A1-A4) and the vault deposit surface. Remove A5 and A6 from the migration.
   Name the affected accounts in the report (D3).
2. Make slice 6 the only migration, straight from owner-split to the final owner-daemon root. *Inference:*
   production has not run the P2b move. Fold C1, C2 and C3 into it, drop the already-owner-box branches,
   and add an explicit "delete after cutover" row for C1-C7.
3. Before cutover, move the Stripe writer to `Plans.apply`, commons publishing to `.published/` and hook
   create/revoke to `HookOwners`. Then delete the per-center `.subscription_state.db`, the shared commons
   read and the old hook path (B15-B17). Make `Plans.read` fail loudly on a missing seeded store rather than
   returning `free`.
4. Add a tasks row that deletes every B item, with a guard that `owner_runtime.current() is None` does not
   appear outside the router entry.
5. Delete A7-A13 (dead launcher and layout readers, the startup consent move, the premise mirror, the
   first-contact fail-open, the legacy-tree deletion check).
