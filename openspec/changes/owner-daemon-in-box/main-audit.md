# Main vs the clean per-user-instance shape: read-only audit

## Slice 2 implementation follow-up (2026-10-16)

The authenticated router now forwards the owner door, MCP transport, REST owner
handlers and inbound hooks through one streaming Unix HTTP proxy. Its only box
lookup is the fenced broker `OWNER_BOX_ROUTE` operation; reservations without a
live admission refuse. The broker persists and rotates per-box secrets, and
`owner_daemon --secret-file` rejects missing/wrong secrets before authentication
or handlers. The router strips public credentials and replaces all identity
headers. D2 decisions in `design.md` record the wire and shared-map contracts.

Deleted from shared serving: moved route registrations, MCP payload inspection
and connection receipts, owner startup/recovery/engine/tick execution, and the
direct unauthenticated SSE/stdio entrypoints. Recovery maintenance now runs in
the owner lifespan. Existing handler modules remain available inside the box.

Focused Linux validation: **155 passed**, covering real broker IPC, two owner
daemon processes, file/MCP/REST reads, B naming A's center, unknown/unadmitted
owners, forged broker fields/fences, durable secret rotation, wrong secrets,
hook token routing, SSE first-chunk delivery and disconnect cancellation,
router-only published/sign-in-shell/pulse routes, and the owner-store guard.
Ruff on changed Python files and `git diff --check` pass. No skips were added.

Test-Removal: retired `test_startup_initializes_before_the_assigned_consumer_starts`
and `test_main_initialization_failure_does_not_start_worker_or_serve`: the shared
entrypoint's owner initialization and direct SSE/stdio paths were deleted;
replacement tests assert no owner initialization and refusal of those transports.
The owner lifespan's storage-order/failure tests remain. The former admitted
router lifespan initialization assertion now asserts no owner-state access.

Remaining integration belongs to later slices: boxhostd mounting the socket and
read-only secret, rotation on start and wake-on-request (4); shared-state broker
publication/revocation of hook digests and shared credential management from
boxes (3); seeding shared maps and splitting existing owner stores (6). No
provisioning, egress, migration or deploy files changed; nothing was pushed.

CI selection and chunk results are recorded below after the local build commit.

## Slice 1 implementation follow-up (2026-10-16)

The owner entrypoint now serves the existing app with relay-derived admission,
local file helpers and `OwnerBoxd` tools. Its acceptance test covers the app
file door, MCP status, a real chat loop writing a file, local bash calling ta
through the real loopback engine endpoint, pause/resume tick dispatch, refused
startup, and a separate CLI process. Non-loopback connects are forbidden in
the in-process acceptance run. Model wires use the existing fixture and its
real in-process broker; no cloud-provenance fixture is used.

Run with `python -m tinyassets.owner_daemon --data <root> --socket <app.sock>`;
`--relay` defaults to the existing `/run/boxhostd/relay.sock` mount.

Linux validation (working tree, no commit or deployment):

| Selection batch | Result |
|---|---|
| 310 files | 6,811 passed, 5 skipped, 2 failed, 2 setup errors; baseline comparison below |
| 155 files, first half of the second chunk | 3,348 passed, 61 skipped |
| 155 files, second half of the second chunk | 2,953 passed, 2 skipped |
| 66 files | 1,511 passed, 3 skipped |
| 18 additional affected files | 346 passed |
| 122 additional affected files | 2,165 passed, 6 skipped |

`affected_tests.py --base origin/main --pr` was run as requested. Because it
compares committed HEAD, the working-tree and untracked paths were also fed to
`--changed --pr`: all 826 selected files are covered above. Lists were stripped
of CRs, missing paths dropped, and split at 310 files. The original second
310-file process exhausted its 1,024-file-descriptor soft limit before pytest
could produce a summary; its identical file list passed in the two 155-file
batches above. No skip or test removal was introduced.

The initial first-chunk failures were SQLite `unable to open database file`
errors in the browser cases `test_manual_update_read_drops_old_selection_or_account_response[account]`
and `test_pending_manual_update_cannot_boot_saved_script_during_refresh[closed_dialog]`,
plus setup for `test_cannot_skip_a_blocked_intermediate_release` and
`test_completed_copy_registers_actual_provenance_automatically`.
The three affected files pass in isolation on both this tree and `origin/main`
(76 passed each). The same full 310-file order against an archive of untouched
`origin/main` (`417f6a4c003db2bfde9cf216cb734eb5e317adc2`), in the same Linux
oracle venue, produced 6,809 passed, 5 skipped, 4 failed and 2 setup errors.
It reproduces the same SQLite failure and the exact blocked-intermediate-release
setup error; other failing case names vary between runs. This is baseline
failure evidence, not a claim that the full selection was uniformly green.

Additional Linux checks: 8/8 owner-daemon acceptance tests; 1,295 passes across
31 affected heavy files; 74 passes in the additional heavy/structural pair;
224 passes in the focused touched-module run. The structural run found the new
state directory missing from storage classification and two file-I/O guard
findings; those were fixed, and the 92-test follow-up (including acceptance)
passes. Ruff on every changed Python file and `git diff --check` pass.

Retained paths to move or delete in later slices (not ported by this slice):

- Center admission and account deletion still select the shared box provider:
  `tinyassets/boxes/lifecycle.py:19`, `tinyassets/account_deletion.py:1006`.
- Native CLI execution/discovery still needs the shared session launch binding:
  `tinyassets/control_plane/native_launch.py:19`,
  `tinyassets/providers/owned_process.py:138`,
  `tinyassets/providers/native_discovery.py:107`. Real credential and model
  egress remains slice 3; `owner_runtime.broker_egress` names its reserved seam.
- Workspace execution and lease reclamation still select a remote provider:
  `tinyassets/workspace_worker.py:202`, `tinyassets/workspace_fs.py:1036`.
  Wake command probes also retain that path: `tinyassets/wake_conditions.py:115`.
- The retained scheduler/engine discovery loops still enumerate serving centers
  from the root database (`tinyassets/control_plane/tick.py:444`,
  `tinyassets/provider_serving_binding.py:1146`,
  `tinyassets/engine_mcp_http.py:396`). In owner mode that root contains only
  this owner's rows; slice 1 adds no owner filter.

The original main audit below is preserved as the pre-change comparison.

Repo: `C:\Users\Jonathan\Projects\wf-isolation-by-box`, HEAD `d31c8293b6a` = origin/main `417f6a4c003` plus one spec-only commit (`openspec/changes/isolation-by-box/`, 4 files, no code). All code citations below are identical on `417f6a4`. Production sha `fa29c95` is not in the local object store, so I could not diff it. Merge order on main (first-parent): C7 #4845 (10-15 00:58), C6 P2b-1..4 (#4947/#4946/#4945/#4944), C6 #4920, C7/C9 binding #4939 (10-15 08:15), C6 prep #5000, **C6 #5043 "delete the owner-cell path"** (10-15), **C10 #4937** (10-16 01:54), C6 #5164, #5166.

Clean shape (report): the shared layer holds only identity, the owner→box map/router, billing, the credential/egress proxy, a next-wake index and published data. The agent loop, history, files, owner DB, schedule rows, pause and owner-data app/MCP logic run inside the box. The shared daemon holds zero host paths to owner data.

**Verdict:** main is an intermediate shape. It has a box for **files and exec**. The **agent loop, conversation history, schedules, pause, subscription/usage state and all app/MCP logic** still run in the shared daemon. After the C6 move, the daemon keeps per-center host stores in four host places. Main has no owner-split runtime left to fall back to.

---

## 1. Where the agent loop runs

**In the shared daemon. The box is called only for files, tools/exec and provider CLIs.**

- `universe_server.converse` admits the turn and calls `session.start(..., OwnerTurn(...))` in the daemon (`tinyassets/universe_server.py:3480-3489`). `session.start` loads the agent definition and runs `_owner_turn` → `universe_intelligence.converse` → `_call_writer` (`tinyassets/control_plane/session.py:115-140, 196-208`; `tinyassets/universe_intelligence.py:1111, 1330, 1601`). Workflow and activity steps run `call_foreground_work_agent` the same way (`session.py:211-240`).
- `session._binding` binds a box handle for the turn and refuses if none is configured (`session.py:143-180`, "owner-box session provider is not configured" at 155-157). `bind_session` mints a `SessionBinding` from `provider.bind(center, account_id, turn_id)` (`tinyassets/control_plane/binding.py:136-145`).
- The loop's event loop is a daemon thread, `ExecutionOwner` "agent-execution" (`tinyassets/agent_loop/execution.py:49-63, 105-114`). Workflow turns use it at `tinyassets/workflow_agent.py:418`.
- The HTTP chat loop is "Serve HTTP chat on the shared loop with tools bound to the turn's box" (`tinyassets/agent_loop/served_chat.py:1-6, 48-80`). The four tools each become one `start_exec` on the bound box (`tinyassets/agent_loop/box_tools.py:1-26, 98-140`). Routing is in `tool_session.py:1-16`. Owner reads (`history`, `activity`) are answered "read-only in the platform process and never forwarded to the box" (`tinyassets/agent_loop/owner_reads.py:1-15`).
- Provider CLIs (Claude CLI, Codex and other external-agent packages) are the exception. They run **inside** the box as an exec through `BoxLauncher` (`tinyassets/providers/owned_process.py:1-14, 127-154`; `tinyassets/control_plane/native_launch.py:9-24`). For those providers the CLI's own inner tool loop runs in the box. The platform turn (admission, journal, history, prompt assembly) still runs in the daemon. `docs/architecture.md:154` (S3) calls this "native CLI loop `claude_provider` ... remain", marked drifted.
- `docs/architecture.md:71` states the as-built shape directly: "`agent_loop/` The thin loop: model turns in the platform, tool calls routed to the box."

**What the box runs:** short, bounded workers, not a resident agent.
- `boxd`/`BoxdCore` is a descriptor-safe file and exec core: read, write, list, stat, remove, start_exec, stream, cancel, export and import (`tinyassets/boxes/boxd.py:1-7, 154-1008`; `OwnerBoxd` at 1111-1140).
- In production, file operations are "one bounded file request, executed ONLY inside the owner's gVisor box" (`tinyassets/boxes/file_worker.py:1-5`). Execs enter through a fixed entry (`exec_worker.py:1`). Code nodes run in `node_runner.py:1-6`. The `ta` client is a credential-free stdio RPC worker (`agent_loop/box_ta_worker.py:1`, trusted end in the daemon at `box_ta.py:1-5`).
- The host (`boxhostd`, root, outside the daemon) owns receipts, generations, quotas and the sandbox lifecycle (`tinyassets/boxes/host_service.py:1`, `owner_files.py:1-6`, `executions.py:1`, `gvisor_runtime.py:1-6`).
- The host caps awake sandboxes at 2 (`tinyassets/boxes/gvisor_files.py:80-94`) and runs execs through a per-owner FIFO that quiesces the box after each command (`executions.py:270-325`; design §12 at `openspec/changes/one-box-per-owner/design.md:561-577`, "The host stops the entire sandbox before recording a terminal execution").

**What the C6 design says the box runs:** the loop, in a later PR.
- The design boundary table puts "The agent loop, one process per session" inside the box and the turn journal and timers outside (`openspec/changes/one-box-per-owner/design.md:37-45`). §3 says "start the loop process in the box" (`design.md:94`). Decisions: "The loop runs in the box" (`design.md:633-635`). ADR-019 agrees: "The box runs the work: the agent loop, its tools, child sessions..." (`docs/decisions/ADR-019-one-box-per-owner.md:31-34`).
- That move is **P3, unbuilt**: `- [ ] 1.4 P3 Loop in the box ... Deletes: the daemon-side loop placement (_call_writer path)` (`openspec/changes/one-box-per-owner/tasks.md:160-180`). P3 depends on C3 and C8 (`design.md:410, 429-434`).
- ADR-022 names "The owner-steered loop runs in the daemon" as an accepted residual between P2b and P3 (`docs/decisions/ADR-022-box-backend-gvisor-on-the-droplet.md:165-180`).
- `target-architecture` D6 still describes the opposite target: the loop "runs in the execution owner (D11)", outside the box (`openspec/changes/target-architecture/design.md:279-296`). The two specs disagree. ADR-019 amends ADR-012 (`docs/decisions/ADR-012-sealed-box-target-architecture.md:5`), so the C6 design is the newer word.

## 2. What is left on the host after `box_layout_migration.apply`

**The whole center tree is copied into the box, and the host tree is kept.**
- The module docstring says "The old trees stay in place" (`tinyassets/box_layout_migration.py:3`).
- For each owner and center, `_copy(data/center, store/<uid>/center)` runs `rsync -aH` of the entire `<data>/<center>/`. It excludes only credential entries (`.credential-vault.json`, `.credentials`, `.credentials.json`, `.oauth-refresh`) and `.runtime` (`box_layout_migration.py:35-37, 402-416, 582-587`).
- Credentials are copied separately to `<data>/.platform/credential-vaults/<center>` (`:434-453, 583`). `.platform/owners/<uid>/agent-home` is copied to `<tree>/.agent-home` (`:588-607`).
- The move then writes `box_records` and `.boxhost/boxhost.db` and the `owner-box` marker. It deletes nothing (`:609-630`).
- The design deletes old trees only after a post-cutover verified backup (`design.md:464-466`). No code on main does that deletion.
- Consequence (inference from the exclude list): the box also receives a copy of every platform dotfile in the center (`.conversation_memory.db`, `.subscription_state.db`, `.pause`, `ledger.json`, `status.json` and so on). The owner can write that copy.

**Per-center platform stores the daemon keeps on the host after the move, by category.** The full name list is in `tinyassets/command_center_layout.py:53-102` and `openspec/changes/isolation-by-box/design.md` D2.

| Category | Host location | Evidence (daemon opens a host path, not the box) |
|---|---|---|
| Conversation (chat history, attention, retrieval) | `<data>/<center>/.conversation_memory.db`, `.conversation_attention.db` | `conversation_store._db_path` = `Path(universe_dir)/_DB_NAME`, then `sqlite3.connect` (`tinyassets/conversation_store.py:75, 121-126`). Converse loads and records through `_universe_dir(uid)` (`universe_server.py:3369-3376, 3445-3449`). Also `conversation_attention.py:49-50`, `conversation_retrieval.py:28` |
| Billing/plan and metering | `.subscription_state.db`, `.usage_ledger.db` | `storage/subscription_state.py:18, 56-63` |
| Requests/approvals, idempotency, receipts, authoring | `.pending_requests.db` (legacy, fenced), `.idempotency.db`, `.external_write_receipts.db`, `.authoring.db`, `.wiki_write_back_destination_markers.db` | `storage/pending_requests.py:49, 307`; `idempotency.py:270`; `authoring/store.py:48` |
| Scheduler and control flags | `.pause`, `.worker_supervisor*.json`, `.runtime_status.json`, `.engine_mcp_*.json`, locks | The tick reads `(base_path / universe_id / ".pause").exists()` on the host (`control_plane/tick.py:975-976`). Status reads `(udir/".pause").exists()` (`api/universe.py:1155`). Pause is **written** through `write_data_path` (`api/universe.py:4106-4110`), which routes to the box when a binding exists and raises `BoxAuthError` when none does (see §3). This is the "written to the box, read from the host" bug |
| Records dir outside `<center>/` (untouched by the move) | `<data>/.agent-sessions/<center>/`: `agent-activities.db` (activities and protected requests), `activity.db`, `history.db`, `steering.db`, owner-control locks | `agent_sessions.py:55, 84-88`; `agent_activities.py:40, 160-162`; `harness_history.py:15`; `agent_steering.py:50`; `storage/request_migration.py:37-48` |
| Sidecars and custody (outside `<center>/`) | `<data>/.universe-sidecars/<center>/` (consents, seed locks), `<data>/.platform/credential-vaults/<center>` | `boxes/layout.py:25-26`; `storage/effector_consents.py:69`; `credential_vault.py:162` |
| Shared root DBs holding per-owner rows | `<data>/.tinyassets.db` (ACL, `universe_owner`, branch definitions), `<data>/.runs.db`, `<data>/.automations.db` (schedule rows), broker DBs | `automations.py:70, 540-541`; `api/market.py:364`; `provider_serving_binding.py:1146-1160`; tick iterates every serving universe (`tick.py:381, 466, 646`) |

Platform records classified as platform state that live inside the center are a mixed case: `status.json`, `activity.log`, `ledger.json`, `work_targets.json` (`command_center_layout.py:55-60`). They go through `read_data_path`/`write_data_path`. Inside a turn they therefore hit the **box** copy, which the owner can write. Outside a turn they fail with `BoxAuthError`. Example: the ledger append in `api/engine_helpers.py:112-121` only logs a warning on failure.

The guard `tests/test_universe_path_io_guard.py:36-40` counts only `read_text`/`open`/`unlink` and similar calls. It does not count `sqlite3.connect` or `.exists()` (inferred from `_RAW_ATTRS`), so these host-store accesses fall outside it.

## 3. App and MCP owner-data reads

**All of it runs in the shared daemon.** A box is reached three ways:

1. **Session binding.** Only `session.start` mints one (`session.py:143-180`). `native_discovery` (`providers/native_discovery.py:108-118, 195-205`) and account deletion (`account_deletion.py:1015`) also bind. The engine MCP server reuses the live session's binding through `active_binding(owner, center)` (`engine_mcp_server.py:4783-4790`; `binding.py:129-133`). So the agent's own tool calls during a turn reach the box.
2. **`file_binding(root)`** (`binding.py:148-166`). It returns the current binding when the root matches. With no binding, any root directly under `data_dir()` raises `BoxAuthError("owner files require an admitted session binding")` (`:162-165`). Every `read_universe_file`/`write_universe_file`/`list_universe_entries`/`read_data_path`/`write_data_path` goes through it (`tinyassets/universe_files.py:115-162, 192-217, 343-398, 655-740`). `read_data_path` re-raises the error as `UniverseFileError` (`:673-678`).
3. **Raw host paths.** `sqlite3.connect`, `Path.exists()`, `os.open` on `<data>/<center>`.

No code on main binds as the requesting account outside a turn. The `#5168` helpers (`owner_box_reads`, `ensure_host_stub`, `platform_center_path`) are absent: a grep finds nothing.

**What breaks or goes stale after the move** (an app/MCP request with no live turn):
- **Raises or returns an error (`BoxAuthError`):**
  - `universe action=control_daemon pause` writes through `write_data_path` (`api/universe.py:4106-4116`). It returns "Failed to write pause signal: owner files require an admitted session binding".
  - `read_output` (`api/universe.py:2010-2028`) returns "was not read".
  - Wiki writes (`api/wiki.py:124-2518`, `write_data_path`), premise and config writes (`api/universe.py:3203, 3583, 4540`), canon meta (`:3795`) and the `ledger.json` append (`api/engine_helpers.py:119`) refuse.
  - `_read_json` on `work_targets.json` in listing/inspect raises (`api/helpers.py:215-234`; `api/universe.py:1151`).
- **Silently wrong:**
  - Universe-listing word counts swallow the `OSError` per file and count 0 (`api/universe.py:1120-1130`).
  - First-contact home materialization degrades to `_no_home("admission")` on a `BoxAuthError` (`api/first_contact.py:168-178`).
- **Stale (reads the host copy, which the move leaves in place):**
  - The app file browser and downloads (`owner_door/files.py:42-51` → `api/universe_file_reads.py:165-177, 129-149`) open `<data>/<uid>` with `os.open`/`listdir` on POSIX. They never touch the box, so after the move they show the frozen pre-move copy and none of the agent's new files.
  - Pause status is read from the host (`api/universe.py:1155`; `tick.py:976`). A pause written during a turn goes to the box, so the scheduler never sees it.
  - Chat history (`conversation_store`) is not stale: it is a host-only platform store that the daemon still owns.
- `owner_door/routes.py:1-14` serves `/app/api/read` and `/app/api/status` through the same domain reads as MCP `read_graph`/`get_status`, so it inherits all of the above.

## 4. Single-owner self-hosted daemon, and the daemon-in-each-box idea

**Self-hosting is not supported.**
- `docs/getting-started/self-hosting.md:1-28`: "the production server is not a supported install". `universe_server.main` refuses unless the process is an admitted cloud runtime (`tinyassets/platform_runtime_provenance.py:1-30`; `universe_server.py:4180-4187`).
- ADR-008 makes the platform cloud-only (`docs/decisions/ADR-008-cloud-only-platform.md:13-29`). README line 15 says the same.
- `tests/smoke/test_selfhost_parity.py` only unit-tests `scripts/selfhost_smoke.py`. That script is the Row F "self-host migration" smoke: canonical URL vs tunnel parity for the host-to-droplet move (`scripts/selfhost_smoke.py:1-25`). It is not a per-owner runtime.

**Single-tenant remnants that would help:**
- `data_dir()` is one env-selected root (`tinyassets/storage/__init__.py:185-215`).
- `is_local_single_tenant`/`is_local_operator_process` mark a tray process (`api/permissions.py:265-276`; `auth/middleware.py:1046-1063`).
- `universe_files` keeps a Windows single-tenant tray fallback (`universe_files.py:18-20, 324-327`).
- Single-tenant defaults remain in `api/helpers.py:154`, `api/universe.py:4410, 4559` and `graph_compiler.py:411`.

**Could the same daemon run inside each owner's box, behind a shared router?** Not as main stands. Blockers, all from main:
1. **No ingress, NIC or credentials in the box.** The box's only channel is one `boxhostd` socket, it has no NIC, and every outbound call crosses the broker (`design.md:53-68`). The daemon serves `/mcp`, `/api` and OAuth itself.
2. **Provenance gate.** `universe_server.main` refuses any process that is not the admitted droplet runtime (above).
3. **The box lifecycle is per-exec, not resident.**
   - At most 2 awake sandboxes (`gvisor_files.py:80-94`).
   - 2 execution slots and 16 admitted jobs across owners, with whole-sandbox stop per command (`design.md:561-577`).
   - Resident processes, checkpoint and suspend come in P3 and P6 (`tasks.md:160-209`).
   - A resident per-owner daemon would need P3 and P6 plus a capacity change. The design itself flags G3 capacity as a possible founder spend (`tasks.md:60-62`).
4. **The daemon is multi-tenant by construction.**
   - Shared root DBs hold every owner's rows (`.tinyassets.db` ACL and `universe_owner`, `.runs.db`, `.automations.db`, broker identity).
   - The tick iterates every serving universe from the root DB (`tick.py:381, 466`; `provider_serving_binding.py:1146-1160`).
   - Splitting these into a per-owner DB plus a shared index is the "P7 Platform tables into `storage/` (S10)" work, unbuilt (`tasks.md:210`; `docs/architecture.md:161`, S10 drifted, `daemon_server.py` 27 tables).
5. **The box API is the daemon's only way into owner files.**
   - Only `boxes/frontend.py` configures a provider. It requires uid 1001, a committed `owner-box` marker and a host-side broker supervisor (`frontend.py:15-30`).
   - `file_binding` forbids host paths under `data_dir()` (`binding.py:162-165`).
   - Inside a box, the daemon would need its own tree as plain local files, but `LocalBoxProvider` is "unisolated ... Production must not instantiate" (`boxes/local.py:1-6`).
   - Every `file_binding`/`BoxProvider` call site would invert to direct I/O. Inference: this is a large but mechanical change, the opposite of #5043's routing.
6. **The planned in-box loop is a process, not the daemon.** P3 plans a "loop process in the box" with the broker in-box endpoint, while the control plane (session registry, timers, journal, inbox) stays outside (`design.md:39-44`; `tasks.md:162-178`). That matches the report's shape (loop in the box, a shared next-wake index outside). It is not a copy of the whole daemon per box.

## 5. C10 (#4937)

**What it changes** (`git show --stat dd5d4ed5d80`: 150 files, +1602/−11853):
- **Paid market retired.** Deleted `payments/actions.py`, `funding.py`, `schema.py`, `settlement_*`, `wallets.py`, `treasury/*` and `gates/actions.py`/`schema.py`. Trimmed `api/market.py` (−642), `attribution/calc.py` and `ta_cli.py`. Removed WebSite token pages, the paid-market spec and `creator-revenue-share`.
- **One MCP registration boundary.** New `control_plane/registration.py`: `engine_server(handlers)` with a fixed middleware stack and aliasing of the four tools (`registration.py:1-56`). `engine_mcp_server.py` and `served_tools.py` were touched.
- **Activities without the Activities workflow branch.** New `control_plane/activity_session.py` (+129). New `Activity(RunStep)` in `session.py`, which reads instructions through `activity_runner.activity_instructions` (`session.py:82-84, 218-220, 243-246`). `activity_runner.py` −231 net.
- **Legacy Activities conversion.** New `tinyassets/activity_shape_migration.py` (+286), plus +93 in `box_layout_migration.py` and a small `deploy_fail_safe.sh` change.

**Dependency on C6 and the owner-box layout: yes.**
- **Runtime.** `activity_instructions` reads `Activities.md`, `Activities.tools.json` and `Activities.error.txt` through `file_binding` and `binding.provider.read_many` (`activity_runner.py:15-52`). Activities start through `session.start`, and `_binding` refuses without a box provider (`session.py:143-157`). C10 merged after #5043 and was built on it (`git merge-base --is-ancestor eb25b981dac dd5d4ed5d80` is true).
- **Conversion.** The "activity conversion" is **C10 code** (commits "Convert legacy Activities under protected deploy gate", "Fence activity conversion by owner and retry missing boxes"). It is wired **only** into C6's `box_layout_migration`:
  - `check` returns 10 for an `owner-box` volume without `activity_shape: v1` (`box_layout_migration.py:168-188`).
  - `apply` calls `convert(...)` in two places: on the owner-split→owner-box move, before the copy, against host centers (`:558-566`), and as a second one-time pass on an already-`owner-box` volume against box roots (`:538-556, 280-309`).
  - `_boxed_activity_roots` resolves roots from `box_records` (`:284-286`).
  - `convert` itself takes a generic `owner_roots` map (`activity_shape_migration.py:175-205`), so it is layout-agnostic in principle. Its only caller is `box_layout_migration.apply`. On owner-split, `_owner_move_preconditions` refuses when an activity center has no admitted owner (`:483-487`).

**Can main deploy with C6's move off and still keep C10? No, not without reverting C6 runtime code.**
- Setting `ACTIVE_BOX_LAYOUT="owner-split"` makes the deploy take the owner-split branch. That branch logs "this candidate does not apply the cutover" and skips `apply`, which also skips C10's activity conversion (`deploy/deploy_fail_safe.sh:1635-1644, 2003-2014`).
- The image would then fail to serve (see §6): no legacy launcher, no box provider, and C10 activities need a binding. Legacy `activities::*` branch work would also never be converted (`activity_shape_migration.py:56-77`).
- Keeping C10 alone would need either:
  - a revert of #5043 plus C10's box-bound parts, or
  - a reworked C10 conversion that runs on owner-split host centers. That is a small change in `convert` (inference), but its runtime still reads through the box.

## 6. The switch, and what main runs with the move off

**The switch is two constants** in `tinyassets/storage_layout.py:52-55`:
- `ACTIVE_BOX_LAYOUT = "owner-box"`. The deploy reads it into `BOX_CANDIDATE_LAYOUT` (`deploy_fail_safe.sh:1635-1644`), which selects the move step (`:2003-2079`; "The candidate constant selects this step. It does not authorize it" at `:2017`).
- `SUPPORTED_BOX_LAYOUTS = {"owner-box"}`. The rollback and candidate-compatibility probe reads it (`deploy_fail_safe.sh:370-410`).
- At runtime, `storage_layout._validated` refuses any marker whose `box_layout != ACTIVE_BOX_LAYOUT` (`storage_layout.py:180-185`).
- Gates that block the move even when the switch is on: a provisioned canary, an idle-turns proof, the XFS store check, preflight and a fresh paired backup (`deploy_fail_safe.sh:1876-1878, 2015-2056`).

**With the move off, main has no working host-path (owner-split) runtime.** #5043 deleted it: "delete the owner-cell, role and jail execution path" (373 files, −44,356 lines). The chain:
- **Entry.** `deploy/box_daemon_entry.py:12-22, 37-39` sends an `owner-split` marker to `/usr/local/libexec/ta-launch.py`. The Dockerfile no longer ships that file ("the image ships no launcher, mapper or cell", `Dockerfile:355-361`; only `ta-box-entry.py` is copied). The entry exits 78 with "this image serves only owner-box data". A fresh volume with no marker hits the same refusal (`:29-31`).
- **Runtime, if the entry were bypassed.** `configure_box_provider` is called only by `boxes/frontend.py:26-29`, which requires the `owner-box` marker (`:15-19`). Without it:
  - every chat turn raises `BoxAuthError("owner-box session provider is not configured")` (`session.py:155-157`);
  - every provider CLI raises `ProviderConfinementError` (`owned_process.py:140-154`; `native_launch.py:24`);
  - every owner-file access under `data_dir()` raises (`binding.py:162-165`).
- **`boxhostd`** idles on an owner-split marker (`deploy/boxhostd.py:25-51`).
- **Inference:** a main image with both constants set to `owner-split` would pass the deploy's layout gates, then fail the health check and roll back. Production would stay on `fa29c95`.
- The C6 design states the intent: "The new image serves only from boxes. There is no flag back to cells" (`design.md:478`).
- `design.md:594-595` still says "`ACTIVE_BOX_LAYOUT` and production compose remain owner-split". That line is stale: the constant is `owner-box` on main.

---

## Gap to the clean shape (by closing shape, from the above)

- **Loop into the box.** This is C6 P3: loop process, broker in-box endpoint, sessions surviving deploys. It deletes `_call_writer`, `ExecutionOwner` and the daemon-side loop placement.
- **Per-center platform state out of `<data>/<center>/`.** `isolation-by-box` D2 moves it to the sidecar. In the clean shape most of it (history, pending requests, pause, automations rows, steering, activity) belongs **in the box**, not in a platform sidecar. Only the subscription/usage/billing rows, consents and the next-wake index stay shared. `isolation-by-box` D4 keeps `.pause` as a platform file, which contradicts the report's "put the flag where its reader lives" rule once the scheduler reads a wake index.
- **App/MCP reads.** Either bind as the requester (`isolation-by-box` D3) or move the owner door into the box. The file browser (`api/universe_file_reads.py`) is the clearest host-path reader to delete.
- **Shared root DBs** (`.tinyassets.db`, `.runs.db`, `.automations.db`) still hold per-owner rows. This is C6 P7/S10.
