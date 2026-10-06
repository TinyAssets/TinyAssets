# Background Branch authority inventory and read-owner map

Freshness: 2026-07-30 America/Los_Angeles, Windows, current main
`74419e561a3c59d68e9113df64a27ab120d7beb8`; production recovery
`30571039375` completed with canonical HTTP 200, exact-seven surface proof, and
durable fence finalization.

## Result

This closes `harden-background-branch-execution-authority` tasks 1.1 and 1.2 in
dark mode. It does not activate background authority, choose a persistence
backend, or change any runtime decision.

The executable closure guard is
`scripts/check_background_authority_inventory.py`. It scans all Python under
`tinyassets/`, `fantasy_daemon/`, and the independently shipped Claude-plugin
runtime for reviewed Branch execution, indirect callback, compiled-stream, and
queue boundaries. It compares the result and per-function occurrence counts to
an exact callsite manifest, resolves imported and assigned aliases, verifies
every source-family/read-owner marker, and proves both shipped `file_bug`
copies call no execution or enqueue boundary.
`tests/test_background_authority_inventory.py` makes that check part of CI and
mutation-proves new roots, duplicate same-function calls, and aliases are
detected.

Original 2026-07-30 scan: 40 sensitive callsite groups (42 occurrences), 12 source
families, 14 canonical/read-classification categories, zero unreviewed
callsites, zero stale entries, and zero filing-to-run calls.

## 2026-10-03 D2 Activities registration

The D2 integration adds exactly one `start -> execute_branch_async` call in
`tinyassets/activity_runner.py` and one in the packaged runtime's copy of that
module. Both have occurrence count one; neither module is excluded from the
scanner. This registration changes no execution decision.

The named `activities` root records the existing owner-bound provider callback,
`owner_run_identity` context and its false-result refusal, `owner_user_id`, and
per-node automation authority guard. It also records the generation-checked
`bind_run` and cancellation on a lost claim, the run-id lookup and unlinked-run
refusal, and the foreground provider's Activities-branch start barrier. The
barrier runs before the agent acts; dispatch starts the run before binding it.

`tests/test_activity_dispatch.py` covers the owner/provider/guard arguments,
owner-loss refusal, stale-generation cancellation, and refusal of an unlinked
run. Inventory regressions remove each recorded guard and add a second launch
to each shipped module, requiring the checker to fail. Literal source markers
are a drift detector; the behavioral tests remain necessary authority evidence.
This registration does not implement account activation or automatic reset
wakes/resumption of paused activities.

## 2026-10-05 L7 ingress HTTP-body registration

`tinyassets/ingress.py::AppIngressMiddleware.__call__ -> request.stream` and
its plugin copy each have one call. These read a bounded Starlette HTTP body,
not a compiled graph stream. The adapter remains inside AuthContextMiddleware;
acceptance and receipt access hold current principal/home/admin authority, and
acceptance grants no execution authority. No execution root, scanner rule or
mutation assertion changes. Registering these exact name collisions keeps the
real graph-stream boundary detectable, including added calls in either copy.
The ingress review and traffic proof live in the zero-impact-deploys L7 artifacts.

## Execution and issuance roots

| Family | Current root / boundary | Authority state and required successor behavior |
|---|---|---|
| Schedule and event | `Scheduler._maybe_fire_schedule` and `Scheduler._dispatch_event` invoke the injected `_run_fn` callback after reading persisted schedule/subscription rows; both source and packaged copies are exact-manifest edges. | Persisted continuation exists, but `owner_actor` and synthetic `scheduler:*` / `subscriber:*` strings are not execution authority. Tasks 3.1-3.7 must derive and revalidate a binding/attempt. |
| Goal subscription | `tinyassets.subscriptions` stores the legacy Goal list; `tinyassets.scheduler` owns event subscriptions. | Treat list membership only as source state. Task 3.10 replaces it with authenticated principal/universe target delegation. |
| Soul / `PROGRAM.md` / compiled cycle | `DaemonController._run_graph` reads `PROGRAM.md` through `read_legacy_premise` and executes the compiled built-in graph through `compiled.stream`; `_try_execute_soul_loop` separately invokes synchronous `execute_branch`. | Compatibility premise, daemon context, and the built-in cycle are inputs, not target authority. Tasks 4.1-4.8 bind the pinned soul source and retire the direct compiled bypass. |
| Request admission | `api.universe._action_submit_request` appends an epoch-1 `BranchTask`; `RequestAdmissionStore` and `Epoch2BranchTaskAdapter` implement the transactional successor. | Admission/queue identity narrows scheduling only. Task 3.9 commits the exact target binding with the Request aggregate; B2 remains separately required. |
| Goal-pool / paid-market producer | `GoalPoolProducer` and `NodeBidProducer` emit through `dispatcher.run_branch_task_producers_into_queue`. | Producer YAML, `posted_by`, and market rows do not authorize execution. Tasks 3.11-3.12 require accepted contract/source generations and prepared target bindings. |
| `BranchTask` claim | `fantasy_daemon._try_dispatcher_pick` calls epoch-1 `claim_task`; epoch-2 exposes inactive transactional claim/recovery. | A queue claim proves possession only. Task 2.5 adds attempt/audience/generation fencing and fail-closed epoch migration. |
| Graph enqueue | `graph_compiler._node_enqueue_branch_run` calls `append_task_capped`. | Public target checks and queue caps are not durable delegated authority. Tasks 5.1-5.5 derive and transfer a bounded child binding before pickability. |
| Live/frozen `invoke_branch` | Compiler closures call synchronous `execute_branch`, `execute_branch_async`, or `execute_branch_version_async`. The async entrypoints converge on `_execute_branch_core`; synchronous `execute_branch` has its own execution body. | Tasks 5.1-5.2 require a non-serializable trusted delegation context and atomic child binding for direct and enqueued children. |
| Direct root run | `api.runs._action_run_branch` and `_action_run_branch_version` call the two run entrypoints. | Current request ACL checks are the input owner; task 6 root issuance must snapshot their result into one server-owned binding/attempt. |
| Resume/recovery | `api.runs._action_resume_run`, `_ensure_runs_recovery`, `runs.resume_run`, `runs.recover_in_flight_runs`, epoch-1 task recovery, and epoch-2 `recover_expired`. | Checkpoint/run/lease state is evidence, never authority. Reissue/reclaim must use the exact predecessor attempt and a conclusive boundary. |
| Actor boundary | `api.permissions.current_request_actor_id` is the request identity read; `api.engine_helpers._current_actor` may fall back to environment-derived host identity. | Request identity is canonical. `_current_actor` output is diagnostic only for background issuance; tasks 3/4/6 remove it from durable authority fields and decisions. |
| Daemon/cloud/distributed worker | `_try_execute_claimed_branch_task` invokes synchronous execution, `cloud_worker.run_supervisor` owns cloud discovery, and the B2 record/verifier contracts remain dark. | Worker descriptors and daemon ownership do not grant target access. Every launch needs the exact attempt plus separately valid B2/provider authority. |
| Selector/leaderboard/market delegate | selector dispatch calls `_execute_branch_core`; leaderboard calls selector dispatch; Goal canonical dispatch delegates to `_action_run_branch_version`. | These are async root-run entrypoints, not alternate trust domains. They must consume the same request-derived root binding and cannot mint authority from ranking/canonical state. |
| Historical wiki forwarding | `bug_investigation.enqueue_investigation_request` remains a retirement-only legacy queue writer. Both shipped `_wiki_file_bug` handlers are filing-only. | It is excluded from logical-key builders, binding, backfill, drain, revival, and execution. The closure guard fails if either shipped `file_bug` copy calls an enqueue/execution boundary. |

## Exact sensitive callsite closure

The guard records these current call edges and exact occurrence counts. A new
edge or an additional call inside an existing function fails CI until this
audit and manifest are reviewed together.

- `fantasy_daemon/__main__.py::DaemonController._run_graph -> stream`
- `fantasy_daemon/__main__.py::DaemonController._try_execute_soul_loop -> execute_branch`
- `fantasy_daemon/__main__.py::_try_dispatcher_pick -> claim_task`
- `fantasy_daemon/__main__.py::_try_execute_claimed_branch_task -> execute_branch`
- `tinyassets/api/market.py::_action_goal_run_canonical -> _action_run_branch_version`
- `tinyassets/api/quality_leaderboard.py::build_quality_leaderboard -> dispatch_selector`
- `tinyassets/api/runs.py::_action_resume_run -> resume_run`
- `tinyassets/api/runs.py::_action_run_branch -> execute_branch_async`
- `tinyassets/api/runs.py::_action_run_branch_version -> execute_branch_version_async`
- `tinyassets/api/runs.py::_ensure_runs_recovery -> recover_in_flight_runs`
- `tinyassets/api/selector_dispatch.py::dispatch_selector -> _execute_branch_core`
- `tinyassets/api/universe.py::_action_submit_request -> append_task`
- `tinyassets/bug_investigation.py::enqueue_investigation_request -> append_task`
- `tinyassets/dispatcher.py::run_branch_task_producers_into_queue -> append_task`
- `tinyassets/graph_compiler.py::_build_invoke_branch_node._node_fn -> execute_branch`
- `tinyassets/graph_compiler.py::_build_invoke_branch_node._node_fn -> execute_branch_async`
- `tinyassets/graph_compiler.py::_build_invoke_branch_version_node._node_fn -> execute_branch_version_async` (two calls)
- `tinyassets/graph_compiler.py::_node_enqueue_branch_run -> append_task_capped`
- `tinyassets/runs.py::execute_branch_async -> _execute_branch_core`
- `tinyassets/runs.py::execute_branch_version_async -> _execute_branch_core`
- `tinyassets/scheduler.py::Scheduler._dispatch_event -> _run_fn`
- `tinyassets/scheduler.py::Scheduler._maybe_fire_schedule -> _run_fn`

The packaged runtime has the same 18 `tinyassets` execution/queue edges above
except the four `fantasy_daemon`-only edges, and includes both scheduler
callback edges. Its versioned-branch compiler closure also has two
`execute_branch_version_async` occurrences. Package parity is scanned
independently; it is not inferred from source-tree equality.

## Canonical read interfaces

The target consumes these owners; it does not create parallel identity, ACL,
queue, B2, or provider truth.

| Read category | Canonical current owner | State consumed by background authority |
|---|---|---|
| Identity | `api.permissions.current_request_actor_id` | Authenticated request principal only. |
| ACL | `api.permissions.universe_access_allows` | Exact universe read/write decision; revalidate just in time. |
| Branch read | `api.branches._resolve_readable_branch` / `_resolve_readable_version` | Canonical request-scoped persisted branch/version visibility; target snapshot is stored separately. |
| Branch write/author | `api.branches._branch_authorized` | Author-only mutation predicate, recorded separately and never described as the read seam. |
| Daemon | `daemon_registry.get_daemon` / `list_runtime_instances` | Canonical daemon/runtime identity records. The chatbot-facing `daemon_control_status` projection is not the background authority read owner. |
| Run | `api.runs._run_read_allowed` / `_run_write_allowed` | Current run visibility/mutation decision and canonical run record. |
| Request/admission | `storage.request_admissions.RequestAdmissionStore` | Transactional Request/task/claim evidence; scheduling only. |
| Filing-only wiki negative | `retire-cheat-loop` `wiki-commons` delta plus both shipped `_wiki_file_bug` handlers | No receipt, task, run, investigation block, or background issuance. |
| Goal subscription | `subscriptions.list_subscriptions` and `scheduler.list_scheduler_subscriptions` | Source membership/generation only; authenticated delegation is a successor task. |
| Paid-market acceptance | `paid-market-track-e-wave-2-transport` paid-market-economy delta | Contract-only until acceptance/delivery owners land; a market row is never authority. |
| Queue | epoch-1 `branch_tasks.read_queue` and epoch-2 `Epoch2OperationalRead` | Scheduling state, lease/generation, and recovery evidence only. |
| B2 | `execution_authority.records.ExecutionGrantV1` plus `RecordVerifier.verify`/`require_authentic` contract | Separate signed owner/daemon/job/capsule/lease/fence execution authority. The record/verifier is implemented as a dark contract; no production B2 store/claim read is yet available, so target authority must fail closed until that owner lands. |
| Provider work | `constrain-set-engine-provider-authority` provider-routing delta | Contract-only durable provider-work hold/receipt owner; no ambient fallback. |
| Provider attempt | `provider-attempt-receipts` provider-routing delta | Contract-only result-local attempt evidence; cannot become execution authority. |

## Cloud-drain implication

The first cloud drain needs only the Request/single-flight subset of these
families, but it must pass through the same closed interfaces. The shortest
implementation order after this inventory is:

1. complete the rendered filing-only production proof so retirement task 2.1
   stops gating the dark store;
2. implement binding/attempt persistence and lifecycle tasks 2.1-2.4 over the
   approved epoch-2 transaction boundary;
3. bind one private main-universe Request/Branch version and Jonathan-owned
   provider/GitHub authority;
4. prove one restart-safe PR-only slice, then cut over with one active epoch;
5. retain the mandatory 24-hour computer-off acceptance interval.
