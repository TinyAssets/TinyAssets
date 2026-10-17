# Main vs the clean per-user-instance shape: read-only audit

## Slice 3 implementation follow-up (2026-10-16)

Owner-mode model and authenticated HTTP calls now acquire the admitted relay,
use the broker's existing grant/policy/accounting operations and stream over
`OPEN`. Source descriptors and allocation liveness live at the broker; these
model-source and accounting reads do not open box-private files. Plan and published-catalog reads
use `BOX_SHARED`. D3 decisions in `design.md` record the protocol choices.

Focused Linux validation: **95 passed** across the new owner-egress tests,
owner daemon app/MCP chat tests, broker server, upstream streaming and usage
IPC tests. This includes a fresh box subprocess model call, secret absence
in its environment and data root, an authenticated effector, revoked grants,
lost accounting-channel liveness, missing broker refusal and socket/HTTP
refusal. The final accounting/owner/router recheck passed **68 tests**, and
the selected AD chunk ran all **18 owner-egress cases**. Ruff, strict OpenSpec
validation and `git diff --check` pass. No tests were retired and no
skip/skipif was added.

Cutover work remains outside this slice: boxhostd supplies the relay mount,
sanitized environment and null network namespace; slice 6 seeds the shared
billing/published stores. The shared Stripe webhook/checkout writer and
commons publishing must be wired to those stores before cutover (these reads
do not migrate either writer). Provider CLIs refuse pending broker-endpoint
authentication; daemon-side subscription/credential refresh is disabled, with
no vault fallback. The founder's slice 7 unported-feature report must include
these remaining writer and authentication paths. D2's hook-digest publication
and shared credential-management integration also remain pre-cutover work;
this slice implements the requested egress and shared-read operations.

Implementation commits: `45e5035341f`, `0cf18d31da2`, `8970069eed7`,
`dd58f98022b`, `ce98cb2beb2`; validation-fixture corrections: `bea903d8cbc`.
No push, deploy, provisioning, waker or migration change was performed. No
agent was dispatched.

After committing, ran `python scripts/affected_tests.py --base origin/main
--pr --out <file>`, stripped CRs, dropped missing paths (none), and ran
`split -l 155`. The final regenerated selection is identical: **900 files**,
five 155-file chunks and one 125-file chunk. Each ordered list ran through
`linux_oracle.py`; all 34 affected heavy files ran separately. Exact lists,
logs and JUnit reports are in `%TEMP%/owner-s3-validation/`.

| Linux selection | Result |
| --- | --- |
| AA, 155 files | 3,505 passed, 12 existing skips, 1 identical main-baseline failure |
| AB, 155 files, complete corrected rerun | 2,715 passed, 2 existing skips |
| AC, 155 files | 3,683 passed, 47 existing skips |
| AD, 155 files | 3,133 passed, 20 existing skips |
| AE, 155 files | 2,841 passed, 1 existing skip |
| AF, 125 files, complete corrected rerun | 2,535 passed, 5 existing skips |
| Affected heavy files, 34 files | 1,359 passed, no skips |

AA's `test_activity_shape_migration::test_conversion_is_per_owner_idempotent_and_restoreable`
failed while archiving a disappearing `.runs.db-shm`. The same 155 files in
the same order on the initial `origin/main` (`417f6a4c003`) produced identical
JUnit failure text (3,504 passed, 12 skips, that one failure). Migration code
was not changed. Main advanced to slice 1 (`e5583246c25`) during validation.

AB exposed a real source-validation regression: a daemon-side caller tried
to open a local accounting DB. The fix reuses the broker's existing admission
transaction and preserves daemon-side grant queries; the entire AB chunk was
rerun successfully.

AF initially exposed two timing-sensitive fixture failures: catalogue expiry
during an authority-only test, and an accounting finalizer timing out during
a workspace test. The two files passed together in isolation (178 passed,
2 skips), and the full ordered main-baseline AF run passed (2,535 passed,
5 skips at `e5583246c25`), so neither was counted as a baseline exception.
The authority fixture now holds its synthetic discovery clock constant; the
workspace fixture collects prior cyclic allocations on the test thread before
an in-process broker can run their synchronous accounting finalizers. The
original assertions and runtime freshness checks are retained. The complete
corrected AF chunk passed with no warnings.

Existing skip reasons include Windows, Linux root/rsync, shellcheck, Docker Compose,
or the explicitly opt-in live CLI venue; two older vault-link fixtures also
skip on their missing destination parent. They are not counted as passes.
The selected and heavy runs total **19,771 passed, 87 existing skips**, and
the one exactly reproduced AA baseline failure. The work remains local.

## Slice 2 review fixes and validation (2026-10-16)

Local implementation: `545139dd00e`. Validation fixture correction: `50323502234`.
Review disposition: AGREE on findings 1–4; finding 5 is deferred to slice 4,
with no secret-start/rotation implementation change. Nothing was pushed or deployed.

- **1, socket:** `resolve_box` defaults to the existing Compose mount,
  `/run/tinyassets-boxhostd/<uid>/app.sock`; a test checks that deployment contract.
- **2, keys:** the router alone writes key records. Owner-mode mint/list/revoke,
  including connected-agent listing, use the bound relay, broker admission and
  broker-only router RPC; grants remain local and the box has no key database.
  Real owner-process tests mint a key through this channel, authenticate `/api`,
  revoke it from the box and observe 401. The fixture no longer copies key rows.
- **3, hooks:** the router mints and revokes the token map through the same channel;
  payload/configuration rows remain local. Box-created tokens reach the owner's
  real socket, and raw-token, prefix and source revocations all yield inbound 404.
- **4, identity:** B's bearer plus A's valid protected cookie returns 401;
  matching bearer/cookie and cookie-only requests still work.
- **Channel admission:** tests refuse owner-field injection, another owner's
  key revocation, a non-broker router-socket peer and a retired box admission;
  another owner's hook-map entry survives a foreign revocation attempt.

`design.md` D2 records the socket default, bound credential channel and sole
router writer, local grants and failure cleanup, hook revocation ordering,
single request identity, slice-4 secret-start deferral and stack-only maintenance
cutover after the `--archive` rehearsal. No deploy or provisioning files changed.

Focused Linux runs: 146 passed; the expanded router/connected-agent/key/hook and
inventory rerun passed 99; the corrected snapshot fixture passed 7. No skips in
these runs. All used `python scripts/linux_oracle.py`.

After the local implementation commit, CI's exact selection command
`python scripts/affected_tests.py --base origin/main --pr --out <file>` selected
889 files. CRs were removed, no paths were missing, and `split -l 155` produced
the ordered chunks below. Regenerating after the fixture commit produced the
same selection byte for byte. Each chunk ran through `linux_oracle.py`.

| Chunk | Files | Passed | Existing skips |
| --- | ---: | ---: | ---: |
| AA, complete corrected rerun | 155 | 3,502 | 12 |
| AB | 155 | 2,709 | 2 |
| AC | 155 | 3,694 | 47 |
| AD | 155 | 3,123 | 20 |
| AE | 155 | 2,855 | 1 |
| AF | 114 | 2,402 | 5 |
| Affected heavy files | 34 | 1,359 | 0 |

The selected chunks total 18,285 passes and 87 existing skips, with zero final
failures. No skips were added and no tests were retired. Logs, exact ordered
lists, JUnit reports and the selection digest are under
`%TEMP%/owner-s2-review-9a6wl8na/`; `final-results.json` references AA's corrected
report rather than replacing its original failed-run evidence.

AA first hit a disappearing `.runs.db-shm` while the activity migration fixture
tarred a supposedly stopped tree. Its SQLite contexts committed without closing;
garbage collection could remove a sidecar during traversal. The fixture now
explicitly closes its SQLite connections. The identical 155-file
order on clean, detached `../wf-c12-rehearse` at `origin/main`
`417f6a4c003db2bfde9cf216cb734eb5e317adc2` passed 3,501 tests with 12 skips: it did
not reproduce the failure, and no baseline exemption is claimed. The complete
corrected branch AA rerun passed, as shown above.

Ruff passes for every changed Python file; `git diff --check` passes. Bare
`python -m ruff check` reports 35 existing findings, identical to detached main
by relative filename, code, line and message; both JSON reports are retained
with the validation evidence. The baseline worktree stayed detached and clean;
the slice-3 worktree was not touched, and no agents were dispatched.

## Slice 2 implementation follow-up (2026-10-16)

The authenticated router now forwards owner-facing app routes, MCP transport, REST owner
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

Test-Removal: retired `test_startup_initializes_before_the_assigned_consumer_starts`: shared owner initialization was deleted; replacement asserts no shared consumer startup, with storage ordering retained in owner-lifespan tests.

Test-Removal: retired `test_main_initialization_failure_does_not_start_worker_or_serve`: the shared owner initialization dependency was deleted; replacement proves router startup does not touch owner storage.

Test-Removal: retired `test_admitted_lifespan_reaches_initialization_in_order`: the router lifespan no longer initializes owner state; replacement asserts that boundary, with owner initialization coverage retained.

Test-Removal: retired `test_both_bearerless_transports_bind_the_local_operator`: shared local-operator serving was deleted; both transports now assert refusal.

Test-Removal: retired `test_staged_bundle_enumerates_seven_under_simulated_admission`: shared stdio serving was deleted; the staged artifact proves refusal even with cloud admission.

Test-Removal: retired `test_staged_bundle_enumerates_without_credentials`: shared stdio serving was deleted; the replacement proves refusal and no server response without credentials.

Remaining integration belongs to later slices: boxhostd mounting the socket and
read-only secret, rotation on start and wake-on-request (4); seeding shared maps
and splitting existing owner stores (6). Review fixes below complete shared key
and hook management from boxes in slice 2. No
provisioning, egress, migration or deploy files changed; nothing was pushed.

Local implementation commits: `38fae347fb9`, `6e3d9ecb220`, `3acb71ee293`,
`b68bc5756e5` (the connection-lifetime validation fix).
No push, provisioning, migration or deploy was performed. The user's explicit
no-delegation instruction applies to this slice; no peer agent was dispatched.

CI selection used `python scripts/affected_tests.py --base origin/main --pr --out <file>`
after the local implementation commits, removed CRs and missing paths, then
`split -l 155`. The original 878 files formed five 155-file chunks and one
103-file chunk; the final selection contains 888 files, with eight added
files and then two connection-fix additions run as supplemental chunks. Every invocation used
`python scripts/linux_oracle.py -- <ordered files> -q --tb=short`.
Logs and exact ordered lists are under `%TEMP%/owner-s2-validation/`.

| Linux selection | Result |
| --- | --- |
| AA, 155 files | 3,348 passed, 4 existing skips |
| AB, 155 files | 2,711 passed, 2 existing skips after the connection-lifetime fix |
| AC, 155 files | 3,621 passed, 47 existing skips |
| AD, 155 files | 3,255 passed, 20 existing skips |
| AE, 155 files | 2,777 passed, 1 existing skip |
| AF, 103 files | 2,188 passed, 5 existing skips |
| Final-selection addition, 8 files | 215 passed, 8 existing skips |
| Connection-fix selection addition, 2 files | 147 passed |
| Affected heavy files, 32 files | 1,330 passed |
| Connection-fix affected heavy files, 7 files | 479 passed |
| Structural targets, 39 targets | 824 passed on the complete corrected rerun |
| Focused owner routing/lifecycle | 155 passed; expanded owner-app/session coverage 111 passed |
| Registry and moved-code regression recheck | 132 passed |
| Retired transport and owner-tree tests | 83 passed |
| Connection lifetime and direct callers, 8 files | 217 passed, including the red-to-green real-SQLite regression |

First-pass failures caused by this slice were old assertions about shared
startup, maintenance source locations, the request-logger constructor and
retired shared transports; their owner-side/refusal replacements retain the
relevant coverage. The new hook map and HTTP stream calls also needed entries
in the existing store/background-call inventories. No skip or skipif was added.

AB first failed in `test_actual_competing_store_writer_is_fenced_until_commit`
(`.runs.db` case), then errored during setup of
`test_change_after_reservation_refuses[retire]`: both raised SQLite `unable to
open database file` in `branch_versions` (2,710 passed and 2 skipped on each run).
Two comparisons in the identical 155-file order on untouched `origin/main`
`417f6a4c003db2bfde9cf216cb734eb5e317adc2` each passed 2,711 tests with 2 skips;
they did **not** reproduce the failure, so this slice claims no baseline exemption.
The second baseline's sampled descriptor count reached 938 under a 1,024 soft
limit. Inspection found that version-store transaction contexts did not close
their SQLite connections. A new real-SQLite regression failed before the fix;
the scoped operations now close their connections, including setup-error paths,
while retaining commit/rollback semantics. This necessary validation fix changes
no storage shape or authorization rule. The complete original AB order then
passed 2,711 tests with 2 existing skips. Its sampled descriptor count in the
previously failing section was 100. No observed failure is carried as a baseline
exemption. Chunk reruns cover the route/lifecycle corrections; the final
connection fix additionally has the complete AB rerun, its direct-caller and
affected-heavy runs, and both added selected files recorded above.

## Slice 1 implementation follow-up (2026-10-16)

The owner entrypoint now serves the existing app with relay-derived admission,
local file helpers and `OwnerBoxd` tools. Its acceptance test covers the app
file door, MCP status, a real chat loop writing a file, local bash calling ta
through the real loopback engine endpoint, pause/resume tick dispatch, refused
startup, and a separate CLI process. Non-loopback connects are forbidden in
the in-process acceptance run. Model wires use the existing fixture and its
real in-process broker; no cloud-provenance fixture is used.

Run with `python -m tinyassets.owner_daemon --data <root> --socket <app.sock> --secret-file <read-only-secret>`;
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


## Task 3c-2a local validation (2026-10-16)

Custody writers, erasers and refresh now live in the broker package. The
HTTP_CONNECT commit carries the secret and prepared revision; broker-side
policy/grant validation precedes custody publication. Rotation uses the stored
scheme and expected incarnation. DISCONNECT erase returns `secrets_removed`
and erases custody before deleting fenced ledger rows. Account erasure uses
broker deletion tombstones, and the router no longer stages vault directories.

The exact requested focused selection plus all changed test files was 88 files:
1,482 passed initially, with 21 failures and 19 environment-dependent skips.
All failures were repaired, then the affected 12-file rerun passed 219 tests
without skips. That includes all 17 Compose parser cases, after copying the
existing local validation image's Docker/Compose tools into the local oracle
image (no repository dependency or CI configuration change). The two permanent
UID erasure cases passed separately under `linux_oracle.py --as-root`.
No unresolved failure or unexecuted skipped case remains in this selection.
The full CI affected selection was not run.

New acceptance: AST guard against external vault writers; six deposit schemes
in a separate broker process with a writer-PID observation and committed grant;
public and registered-client OAuth refresh on calls from a separate owner-box
process; unknown REFRESH/REFRESH_ACK refusal; broker-only OAuth environment
rendering. Existing refresh concurrency, storage failure, disconnect retry,
rotation policy preservation and lifecycle tests were adapted to broker custody.

Deleted protocol-only tests from `test_broker_refresh.py`:
`test_foreign_stale_and_revoked_refresh_never_spends` (daemon callback admission),
`test_lost_ack_keeps_rotation_and_does_not_replay`, and
`test_broker_without_daemon_callback_cannot_refresh_locally`.
The rejected-digest test now checks local rejected-token refresh. Existing grant
and fence suites continue to cover denied dispatch. The old create-fault orphan
test now requires no deposit on a failed ledger mutation. Vault deletion tests
now check broker tombstones and locking, replacing daemon-lock staging,
nonhome-admin and absent-owner-schema assumptions. The record-builder guard
now names broker deposit/refresh modules. A pre-existing router-session I/O pin
was brought up to date for its shared session files. The streaming thread check
permits unrelated threads to exit while still forbidding new worker threads.

Also deleted: daemon `_deposit_http`, the old `broker/refresh.py` callback
preparation, the daemon OAuth service's refresh operation, refresh frames and
client callback plumbing, and the standalone Slack vault-deposit script.
Slack diagnostic scripts now use existing broker custody only.

Task 6 must migrate credential ownership/deletion metadata into
`.broker/credential-custody.db` and change credential-vault directory ownership
to the broker uid. Existing HTTP secret files remain in place. The environment
installer renders only OAuth configuration into `broker.env`; no provider code
or host provisioning was added. RFC 7009 is 3c-2b. Remaining door flows, box
secret-free actions, full CI, independent review and cutover remain with their
respective lanes/lead. No sub-agents, external reviewers, push, merge, branch
switch or deployment were used for this task.

## Task 3c-2b partial build (2026-10-16)

Local commits `5477624cf28` and `d6605267a55` implement provider revocation
and standalone `/app/connections` controls. `SOLE_CENTER` is a fenced broker
identity query; explicit centers still use `ADMITTED_OWNER`. Unknown, foreign,
retired and ambiguous centers return 404 at the door. The handler reads no
owner home, ACL, provider assignment or disconnection store. Protected-cookie
and bearer identity agreement is retained at this now-shared handler.

`DISCONNECT` erasure invokes RFC 7009 under the custody lock, with the current
refresh token or otherwise the access token, and registered client auth from
the trusted directory. A provider error never prevents local erasure. Its
fixed failure code remains on the revoked connection row (`revocation_error`,
`custody_erased`); `HTTP_CONNECT` replaces that receipt on a fresh connect.
`CONNECTION_CATALOG(include_revoked=True)` keeps interrupted local cleanup
reachable at the door without the former owner-side disconnection read.

Deleted paths: the connection handler's `_read_home`, founder-home/admin
checks, `remove_http` composition and `unfinished_disconnections` read. The
box's existing removal composition is unchanged. No tests were deleted.
Catalog and connection-lifecycle fixtures now seed broker admission; the
deleted-account deposit fixture uses broker erasure. Structural inventories
were reconciled with earlier custody/CLI deletions, and the platform-call spy
fixture now uses HTTP sources instead of retired executors.

New tests: `tests/test_connection_door.py` covers an empty router root,
admission selection, foreign centers, identity conflict, stale incarnation,
fenced cleanup retry and broker query fencing. `tests/test_broker_revocation.py`
adds `test_disconnect_revokes_current_token_then_erases` for public and
client_secret_post clients, each with refresh-token and access-token cases;
it also covers HTTP/network/directory failures, absent endpoint, stale fence,
erased-grant call refusal, idempotent removal and fresh reconnection.
These are not the requested complete per-auth door-to-box lifecycle tests.

The requested focused selection plus structural guards and touched tests was
121 files: 2,447 passed, 17 failed, 3 setup errors and 2 root-only skips on the
initial snapshot. Both skipped cases subsequently passed with
`linux_oracle.py --as-root` (the two permanent-UID cases in
`test_owner_box_destroy.py`). The first six-file implementation run passed 65;
the subsequent door/catalog/revoke run passed 27. The eleven-file repair run
passed 153 with one remaining provider-resolution fixture failure. No skip,
xfail or quarantine was added; the full CI affected selection was not run.
The final `test_platform_has_no_llm.py` rerun passed all 16 after using a real
HTTP definition/grant and a synthetic executor. Every initial failed/error case
is now covered by a passing repair rerun, and both skipped cases executed as
root. Ruff passed on all 21 changed Python files. The final full selection was
not repeated; validation is the initial selection plus these focused reruns.

Remaining work, explicitly uncompleted:
1. Move `/app/model-connect/*` (including popup, source sign-in, presets,
   generic OAuth/DCR and registered clients) to the standalone shared door;
   remove its home/pending/provider-store dependencies and apply admission
   center selection there. Only `/app/connections` moved in this build.
2. Move authorization-code/token exchanges into the broker, including preset
   PKCE exchange, and remove router token exchange/client-secret custody.
3. Remove secret parameters from box MCP and approval connect schemas,
   implementations, instructions and docs; return door links for secret/sign-in
   schemes and use bound-channel keyless HTTP_CONNECT.
4. Carry opaque `request_ref` through broker connection/grant rows and ledger
   queries, and resolve the box's pending connect request from that ledger.
5. Add the full nine-auth-type separate-process door-to-box connect/call/
   refresh/disconnect acceptance matrix and router flow-consumption assertions;
   update/delete superseded connect tests with a deletion inventory.
6. Tick 3c-2 and replace its partial as-built note only after those are complete.

No reviewer/sub-agent, new worktree, push, merge, branch switch, git-config
change or deployment was performed.


## Task 3c-2c build (2026-10-16)

Implementation commit: `d8fa40b9dae`.

`/app/model-connect/` is a shared door prefix. Preset PKCE begin/exchange/resume,
key deposit, inline popup begin/poll/cancel, generic OAuth with public DCR,
registered clients, source sign-in and the public client metadata document now
work without a home, pending-request, provider-assignment or universe-store read.
The authenticated owner selects a live admitted center, using `SOLE_CENTER` when
omitted; unknown/foreign centers return 404. Definitions come from installed
source/acquisition data, credential-free input or a broker catalog query. The
browser preserves the selected center across authorization redirects.

`HTTP_CONNECT` adds door-only `oauth_offer`, `oauth_exchange` and
`preset_exchange` actions. The broker resolves registered-client metadata and
performs authorization-code/token and preset-key exchanges, then commits custody
and the grant. Registered client secrets remain in `broker.env`; the environment
installer now excludes the OAuth namespace from `daemon.env`. Browser redirects,
state/challenges, frozen public definitions and sealed popup verifiers remain at
the door. Redemption consumes the flow before IPC; responses contain only a
credential-free projection. Key deposits use the existing prepare/commit action.
The opaque `request_ref` is stored on the connection row and returned by commit,
resume and `GRANTED_RESOURCE`; the door never resolves a pending request.

Deleted production paths: router token exchange and token-bundle encoding in
`connection_oauth.flow`; router preset exchange transport (moved into
`broker/acquisition.py`); home/ACL/pending composition in `model_connect`;
`model_bootstrap.complete_bootstrap` and `_resume_key`; source sign-in's
`raise_sign_in_ask`, `is_canonical_sign_in` and `offer_signed_in_source`.
Owner-side model-access helpers remain for the next task. No provider-specific
exchange code was introduced.

Deleted tests asserting those removed compositions:
- Entire `tests/test_model_bootstrap.py`: `test_catalogue_failure_is_resumable_without_another_oauth_exchange`,
  `test_concurrent_manual_and_oauth_keys_have_one_deposit_winner`,
  `test_deposit_composes_existing_request_but_does_not_answer_or_serve`,
  `test_disconnect_then_guided_reconnect_requires_fresh_approval`,
  `test_empty_resume_requires_authorization_not_generic_server_error`,
  `test_home_rebinding_refuses_new_deposit`,
  `test_owner_approval_uses_existing_enable_path`,
  `test_partial_activation_resumes_same_request_without_rotating_custody`,
  `test_partial_bootstrap_removal_invalidates_old_pending_approval`, and
  `test_second_oauth_result_cannot_replace_first_deposit`. Its shared fixture
  moved to `tests/support/model_connect.py`.
- `test_manual_catalogue_never_selects_paid_model` in the manual-connect suite.
- `test_sign_in_on_a_powered_command_center_asks_to_add_it` and
  `test_post_sign_in_database_failure_preserves_success` in the source sign-in
  suite. Direct source authorization and broker custody replace the old
  pending-ask/automatic-serving assertions in the remaining source tests.

Replaced/renamed tests (their former names no longer assert the deleted path):
`test_manual_owner_reaches_existing_approval_then_connects`,
`test_manual_does_not_replace_existing_setup`,
`test_deletion_between_ingress_scope_and_durable_begin_cannot_recreate_metadata`,
`test_new_owner_begin_provisions_own_empty_home_without_llm`,
`test_one_tap_raises_the_platforms_ask_from_installed_data`,
`test_sign_in_on_an_unpowered_command_center_serves_on_it`, and
`test_rail_sign_in_uses_server_provenance_for_relay`.

Manual reconnect/approval tests now verify broker grant reuse and resume.
New-owner tests require admission without bootstrapping a home; retirement
races are tested against the admission map. OAuth, popup, persistence and
router-schema fixtures now use the standalone definition and broker contract.

New acceptance tests in `tests/test_model_connect_door_lifecycle.py`:
- `test_door_exchange_box_call_refresh_disconnect[dcr]`
- `test_door_exchange_box_call_refresh_disconnect[registered]`
- `test_door_exchange_box_call_refresh_disconnect[preset]`
- `test_source_sign_in_uses_client_metadata_without_owner_state[dcr]`
- `test_inline_popup_flow_at_empty_door_polls_and_cancels[preset]`

The three lifecycle cases use separate broker and box processes with fake
providers and no provider network. They cover door connect, a box effector
call, broker refresh for OAuth, disconnect, RFC 7009 when configured and refusal
of the next box call. Credentials are generated only inside the broker process;
router token/secret/store access seams fail the test. The router root contains
only shared runtime/flow state, and flow consumption, opaque request references,
replay refusal and foreign-center 404s are asserted.

Remaining scope is unchanged: box-side secret-free connect actions and door
links, completion of the box's pending request from `request_ref`, and the other
six authentication lifecycle cases are the next task. Do not tick all of 3c-2
until that work is complete. Full CI, independent review, migration/cutover,
deployed-SHA assertion and the live naive-user pass remain with the lead.
This build is local only; no sub-agents, new worktree, push, merge, branch switch,
git-config change or deployment was performed.

Validation: the requested patterns, every structural-guard test and the touched
tests comprised a 115-file focused Linux selection: **2,327 passed, 3 failed,
2 root-only skips**. The three failures were a source-card admission fixture and
two deployment assertions that still expected registered clients in daemon.env.
The final six-file repair run passed **164 tests**, including all three failed
cases, all five new door tests using the real box effector, hosted PKCE, generic
OAuth/browser callback tests and source sign-in. Both root-only cases in
`test_owner_box_destroy.py` passed separately with `--as-root` (**2 passed**).
The additional source-pooling run passed 48 with one normalized-policy assertion
failure subsequently fixed and covered by the final 164-pass run. The prior
six-file door run passed 130 with one exact response-shape assertion failure,
also fixed and covered by the final run. All failures in the focused selection and all root-only skips now have
a passing execution; obsolete path assertions are deleted as listed above.
No skip, xfail or quarantine was introduced. The full CI affected selection was not run or claimed. Ruff passed on
all **33 changed Python files**, the background-authority inventory is clean,
and `git diff --check` passed.

After the implementation commit restored the original non-ASCII verifier test
value caught by the commit hook, the complete hosted-auth file passed again:
**51 passed** on Linux. The commit's import-graph, path-resolver, provider-drift
and skill hooks passed; final ruff still passed all 33 changed Python files.


## Task 3c-2d build (2026-10-16)

Implementation commit: `b6e2c91cac4`. Its import-graph, path-resolver,
provider-drift, skill and text-encoding commit hooks passed.

3c-2 is complete locally. Box `connect_http` and `rotate_http` no longer take
credentials; the old secret-taking implementations, `allow_oauth2`/`allow_keyless`
switches, `answer_connect_with_token`, `_assembled_secret`, `_rotate_answer`,
box-side `_with_sign_in_offer`, the keyless vault marker and `connect_source`
helper are deleted. Secret/token/key/password/capability-URL values are absent
from the box connect schema and rejected by connect approvals. The box returns
an `/app` link containing request, center and connection ID. Generic browser
connect, rotation and model-endpoint forms post secrets directly to the shared
door. The keyless action commits through HTTP_CONNECT and dispatch needs no vault.

The existing continuation tick and pending-request read reconcile broker
GRANTED_RESOURCE rows by request_ref and policy. They apply connection uses and
resolve through the existing answer/wake transaction. Failures remain pending
and are returned explicitly. Rotation also records request_ref. The door reads
no owner request and the box reads no router state; no push path was added.

New tests in `tests/test_model_connect_door_lifecycle.py`:
- `test_other_auth_connect_box_call_disconnect` (none, bearer, basic, header,
  oauth1a, url_secret; three door disconnects and three box disconnects).
- `test_box_action_and_approval_link_then_ledger_completion` (links, rejected
  secret values, unmatched/matched request_ref, repeat polling and no router read).
- `test_box_connect_schema_has_no_secret_parameters`.

Existing credential tests now enter through the shared door, then poll the box
ledger. OAuth tests discover at the door; source-pooling fixtures separately
apply model consent inside the owner context. Browser tests fail if credential
values are sent through the approval/MCP transport. A structural-guard fixture
that captured a mocked `/fake` path during imports now uses a per-test data root.

Deleted tests for the removed in-box token-answer path:
- `tests/test_consent_owner_answers.py::test_oauth_token_answer_requires_owner_proof_before_deposit`
- `tests/test_generic_oauth_connections.py::test_the_stored_token_url_is_pinned_to_the_discovered_one`

Deleted stale bootstrap/automatic-serving tests whose implementation was removed in 3c-2c:
- `tests/test_notification_is_the_setup.py::test_the_guided_sign_in_round_trips_to_a_serving_universe_with_no_llm_call`
- `tests/test_notification_is_the_setup.py::test_reconnect_after_a_product_disconnect_serves_again`
- `tests/test_notification_is_the_setup.py::test_after_the_approval_a_turn_selects_a_free_tool_model_on_its_own_connection`
- `tests/test_notification_is_the_setup.py::test_the_free_model_approval_names_no_internal_ids`
- `tests/test_notification_is_the_setup.py::test_a_disconnected_universe_turn_is_setup_not_a_mystery`

Renamed and ported tests (their assertions now exercise the door):
- `test_card_connects_with_one_key_and_returns_unanswered_consent` ?
  `test_card_connects_with_one_key_at_door` in `test_free_source_cards.py`.
- `test_oauth_is_the_primary_action_when_the_provider_offers_it` ?
  `test_oauth_discovery_begins_at_the_door` in `test_generic_oauth_connections.py`.
- `test_a_rotation_needs_a_box_to_paste_into` ?
  `test_a_fieldless_rotation_returns_the_door` in `test_replacing_a_rejected_credential.py`.

Validation (focused only; no full affected selection):
- Requested patterns, every GUARDS test and all touched test files are covered;
  the initial 161-file Linux selection ran 3,416 cases: **3,318 passed, 96 failed,
  2 root-only skips**. Collection first exposed two stale imports of the already
  deleted model-bootstrap module; they now use the shared owner fixture.
- The five-file core repair passed **202**, with four failures subsequently fixed.
  The broader repair passed **930**, with 17 failures subsequently fixed.
- Final lifecycle/OAuth/browser/guidance run: **184 passed**.
- Final continuation/pending/model-use/broker/rotation/approval run: **362 passed**,
  with three receipt/ASGI-fixture failures fixed and covered by the last two-file
  run: **153 passed**. All original failures now have passing executions or
  ported replacement tests; none were skipped, xfailed or quarantined.
- Both root-only `test_owner_box_destroy.py` cases passed separately with
  `linux_oracle.py --as-root`: **2 passed**. A skip is not counted as a pass.
- Ruff passed on all **40 changed Python files**; JavaScript syntax, the channel
  ratchet, `git diff --check` and `openspec validate owner-daemon-in-box --strict`
  passed.

Linux logs, JUnit and selection manifests are outside the repository at
`C:/Users/Jonathan/AppData/Local/Temp/ta-3c2d-7vcbyijm/`.
The full CI selection, independent review, deployment/cutover, deployed-SHA
assertion and live naive-user pass remain with the lead. This task did not push,
merge, change branches, create a worktree or dispatch another agent.
