"""Every periodic loop in the platform, classified (design D7: boxes keep no timers).

Test data, not runtime: it names modules by path, and nothing in the platform
reads it. ``tests/test_control_plane_inventory.py`` scans ``tinyassets/`` for loops that
wait on a clock (a ``while`` containing ``sleep``/``wait``) and for
``threading.Timer``, and fails on any site missing from :data:`SITES` or any
entry whose site is gone. A new timer therefore has to be classified here, in
review, before it can land.

Classes:

* ``CONTROL_PLANE`` -- an always-on duty of the execution owner. Today it runs
  in the one serving process; S8 puts each under the owner lease (the trigger
  pump already checks it, ``scheduler.py``). It reads platform state.
* ``CALL_SCOPED`` -- a bounded wait inside one call, run or lock acquisition.
  It ends with its caller, so it is not a timer: nothing schedules itself.
* ``DELETE`` -- a loop that should not exist in the target shape (fleet-era or
  host-run, "no host writer ever"); listed so it is visible, removed by its
  owning lane.
* ``CLIENT`` -- runs on the owner's own device (the desktop tray), never in the
  control plane or a box.
* ``BOX`` -- a timer inside a command center's box or jail. **Forbidden**: the
  test fails if any entry carries it. A box is woken only by the control plane
  (``tinyassets/control_plane/wake.py``), and in-box processes freeze at
  checkpoint (D4).
"""

from __future__ import annotations

CONTROL_PLANE = "control_plane"
CALL_SCOPED = "call_scoped"
DELETE = "delete"
CLIENT = "client"
BOX = "box"
CLASSES = frozenset({CONTROL_PLANE, CALL_SCOPED, DELETE, CLIENT, BOX})

#: ``"<path>::<qualname>"`` -> (class, note). ``[Timer]``/``[call_later]`` mark a
#: callback-scheduling site; ``#n`` is the n-th clock-driven site in one function.
CLASSIFICATION: dict[str, tuple[str, str]] = {
    # -- always-on duties of the execution owner ------------------------------
    "tinyassets/runtime/assigned_queue_consumer.py::AssignedQueueConsumer._run": (
        CONTROL_PLANE,
        "the owner tick: pumps due automations and control-plane triggers, under "
        "the owner lease; per-universe heartbeat/.pause still touch the universe "
        "directory until the cutover (#4262) moves them to .platform/",
    ),
    "tinyassets/universe_server.py::main._resume_request_loop": (
        CONTROL_PLANE,
        "server-owned protected request recovery and due owner-bound continuations; "
        "per-home control/attempt locks serialize work, not a box timer",
    ),
    "tinyassets/universe_server.py::main._served_budget_lease_loop": (
        CONTROL_PLANE,
        "run-file retention, admitted-run, delivery, budget-lease reconciliation "
        "and stored presentation-policy updates under the service writer barrier",
    ),
    "tinyassets/api/runs.py::start_run_owner_watcher._watch": (
        CONTROL_PLANE, "dead-owner run recovery and terminal-event redelivery",
    ),
    "tinyassets/engine_mcp_http.py::start_engine_mcp_http_servers._supervise": (
        CONTROL_PLANE, "per-command-center engine MCP server supervision",
    ),
    "tinyassets/runs.py::_workspace_sweeper_loop": (
        CONTROL_PLANE, "workspace lock/lease sweep",
    ),
    "tinyassets/workspace_staging.py::start_sweeper._loop": (
        CONTROL_PLANE, "workspace staging sweep",
    ),
    "tinyassets/universe_seats.py::_refresh_loop": (
        CONTROL_PLANE, "account seat stamp refresh while a seat is held",
    ),
    # -- bounded waits inside one call -----------------------------------------
    "tinyassets/ui_preview.py::_supervised": (
        CALL_SCOPED,
        "one requested preview polls its child tree until exit, wall deadline "
        "or a resource breach; no scheduled or autonomous preview work",
    ),
    "tinyassets/ui_preview.py::_supervised#2": (
        CALL_SCOPED,
        "the same preview waits at most ten seconds for namespace descendants "
        "to stop during cleanup, then refuses further previews if uncontained",
    ),
    "tinyassets/owner_lease.py::_lock_blocking": (
        CALL_SCOPED, "one owner-tree gate acquisition retries until timeout_s",
    ),
    "tinyassets/owner_lease.py::acquire": (
        CALL_SCOPED,
        "one owner-key acquisition waits for a live holder only until wait_s; "
        "it returns a lease or refuses, without scheduling work",
    ),
    "tinyassets/agent_turn_coordinator.py::AgentTurnCoordinator._pause_before_retry": (
        CALL_SCOPED,
        "bad-reply backoff bounded by the remaining turn deadline; polls Stop "
        "at most every 0.25 seconds and ends with this turn",
    ),
    "tinyassets/runtime/assigned_queue_consumer.py::AssignedQueueConsumer._refresh_lease": (
        CALL_SCOPED, "agent lease refresh for one running automation batch",
    ),
    "tinyassets/auto_ship_ledger.py::_file_lock": (CALL_SCOPED, "lock acquisition"),
    "tinyassets/broker/server.py::_Connection._refresh": (
        CALL_SCOPED,
        "one broker stream waits for the daemon's refresh answer only until "
        "that stream's own deadline; nothing reschedules",
    ),
    "tinyassets/role_node.py::run": (
        CALL_SCOPED, "one node-sandbox cell is supervised until exit or its call deadline",
    ),
    "tinyassets/role_package_cell.py::run": (
        CALL_SCOPED, "one package payload is polled for exit, limits or broker revocation",
    ),
    "tinyassets/role_tools.py::run": (
        CALL_SCOPED, "one TOOL cell is drained and reaped within its requested wall bound",
    ),
    "tinyassets/activity_runner.py::linked_activity": (
        CALL_SCOPED,
        "one activity run waits up to wait_s for the dispatcher to bind its "
        "record, then returns or refuses before inference; no background scheduling",
    ),
    "tinyassets/bid/execution_log.py::_exec_log_lock": (CALL_SCOPED, "lock acquisition"),
    "tinyassets/bid/node_bid.py::_bid_file_lock": (CALL_SCOPED, "lock acquisition"),
    "tinyassets/boxes/local.py::LocalBoxProvider._supervise": (
        CALL_SCOPED,
        "host-side thread for one exec; stops at leader exit, cancel, wall or "
        "output bound, then kills/reaps the group; not an in-box scheduler",
    ),
    "tinyassets/boxes/local.py::LocalBoxProvider.destroy": (
        CALL_SCOPED,
        "one destroy waits on its finite victim set after cancellation, with "
        "a per-exec destroy_wait_s bound before refusing removal",
    ),
    "tinyassets/boxes/local.py::LocalBoxProvider.export": (
        CALL_SCOPED, "one snapshot waits for pending mutations up to busy_wait_s",
    ),
    "tinyassets/boxes/local.py::LocalBoxProvider.read_many": (
        CALL_SCOPED, "one coherent read waits for pending mutations up to busy_wait_s",
    ),
    "tinyassets/boxes/local.py::LocalBoxProvider.stream.events": (
        CALL_SCOPED,
        "caller-consumed iterator for one bounded exec; ends on terminal or "
        "restore state, optional caller timeout, or iterator close",
    ),
    "tinyassets/branch_tasks.py::_file_lock": (CALL_SCOPED, "lock acquisition"),
    "tinyassets/broker/fence.py::Fence.barrier": (
        CALL_SCOPED,
        "one verified fence barrier cancels older streams then waits for active "
        "send guards to release the condition lock; no periodic wake",
    ),
    "tinyassets/broker/fence.py::Fence.send": (
        CALL_SCOPED,
        "one network-write guard waits for the barrier writer, then rechecks "
        "generation/token and releases its reader count after the send",
    ),
    "tinyassets/broker/server.py::_Connection._pump_body": (
        CALL_SCOPED,
        "outer response pump for one admitted stream; ends at EOF, cancel, "
        "fence refusal or the stream's absolute deadline",
    ),
    "tinyassets/broker/server.py::_Connection._pump_body#2": (
        CALL_SCOPED,
        "inner credit wait for the same response; condition wait is bounded by "
        "the remaining stream deadline and cancellation wakes it",
    ),
    "tinyassets/credential_refresh.py::_hold_vault": (CALL_SCOPED, "lock acquisition"),
    "tinyassets/credential_refresh.py::_refresh_locked": (CALL_SCOPED, "single-flight wait"),
    "tinyassets/credential_refresh.py::file_lock": (CALL_SCOPED, "lock acquisition"),
    "tinyassets/effectors/__init__.py::EffectChain.settle": (CALL_SCOPED, "effect settle wait"),
    "tinyassets/engine_mcp_http.py::wait_for_engine_mcp_route": (CALL_SCOPED, "startup wait"),
    "tinyassets/engine_mcp_server.py::_read_run_settled": (CALL_SCOPED, "run settle wait"),
    "tinyassets/execution_authority/blob_proof.py::_lock_fd": (CALL_SCOPED, "lock acquisition"),
    "tinyassets/node_sandbox.py::NodeSandbox.run_sync": (CALL_SCOPED, "child process wait"),
    "tinyassets/node_sandbox.py::_watch_process_tree_rss": (CALL_SCOPED, "child RSS watch"),
    "tinyassets/provider_admission.py::_acquire_waiting": (CALL_SCOPED, "slot wait"),
    "tinyassets/provider_admission.py::blocking_provider_child": (CALL_SCOPED, "slot wait"),
    "tinyassets/provider_admission.py::provider_slot_async": (CALL_SCOPED, "slot wait"),
    "tinyassets/provider_assignment.py::ProviderAssignmentAdmission.exclusive": (
        CALL_SCOPED, "admission wait",
    ),
    "tinyassets/provider_assignment.py::ProviderAssignmentAdmission.shared": (
        CALL_SCOPED, "admission wait",
    ),
    "tinyassets/providers/codex_provider.py::_stream_codex_exec": (CALL_SCOPED, "stream poll"),
    "tinyassets/providers/owned_process.py::_watch_disk.watch": (
        CALL_SCOPED,
        "host-side disk-budget watch for one jailed provider launch; ends with "
        "proc.wait or budget-breach family teardown and settles that launch's budget",
    ),
    "tinyassets/run_file_upload.py::StreamBridge.chunks": (CALL_SCOPED, "upload stream"),
    "tinyassets/run_file_upload.py::StreamBridge.push": (CALL_SCOPED, "upload stream"),
    "tinyassets/runs.py::await_run_events": (CALL_SCOPED, "caller waits on a run"),
    "tinyassets/runs.py::poll_child_run_status": (CALL_SCOPED, "caller waits on a child run"),
    "tinyassets/scoped_reset.py::acquire_maintenance_barrier": (CALL_SCOPED, "barrier wait"),
    "tinyassets/soul_edit.py::_soul_lock": (CALL_SCOPED, "lock acquisition"),
    "tinyassets/storage/conversation_custody.py::_checkpoint_truncate": (
        CALL_SCOPED, "WAL checkpoint retry",
    ),
    "tinyassets/storage/conversation_custody.py::_configure": (CALL_SCOPED, "WAL switch retry"),
    "tinyassets/storage/run_execution_lock.py::RunExecutionGuard._retire_uses": (
        CALL_SCOPED, "drain wait",
    ),
    "tinyassets/storage_accounting.py::_enable_wal": (CALL_SCOPED, "WAL switch retry"),
    "tinyassets/storage_layout.py::_admit": (
        CALL_SCOPED,
        "one startup/check admission retries lock acquisition and rereads the "
        "migration marker; returns on stable layout or raises on refusal, "
        "without scheduling background work",
    ),
    "tinyassets/subscriptions.py::_file_lock": (CALL_SCOPED, "lock acquisition"),
    "tinyassets/ttl_memo.py::TTLMemo.get": (CALL_SCOPED, "single-flight wait"),
    "tinyassets/universe_seats.py::_wait_for_seat": (CALL_SCOPED, "seat wait"),
    "tinyassets/universe_tools.py::_remove_cgroup": (CALL_SCOPED, "cgroup teardown retry"),
    "tinyassets/universe_tools.py::_slot": (CALL_SCOPED, "tool slot wait"),
    "tinyassets/universe_tools.py::_watch": (
        CALL_SCOPED, "watches ONE jailed tool call from outside the jail",
    ),
    "tinyassets/workspace_family.py::family_fence": (CALL_SCOPED, "fence wait"),
    "tinyassets/workspace_fs.py::_retry_transient_windows": (CALL_SCOPED, "Windows retry"),
    "tinyassets/workspace_pool.py::admit": (CALL_SCOPED, "admission wait"),
    "tinyassets/workspace_provision_process.py::run_provision_stage": (
        CALL_SCOPED, "provision stage wait",
    ),
    "tinyassets/workspace_registry_process.py::RegistryBrokerProcess.finish": (
        CALL_SCOPED, "broker shutdown wait",
    ),
    "tinyassets/workspace_staging.py::_lock_tree_exclusive": (CALL_SCOPED, "lock acquisition"),
    # Bounded retry loops (``for attempt in range(n): ... sleep``).
    "tinyassets/api/wiki.py::_wiki_file_bug": (CALL_SCOPED, "bounded retry"),
    "tinyassets/conversation_store.py::_record_pair": (CALL_SCOPED, "bounded retry"),
    "tinyassets/conversation_store.py::backfill_once": (CALL_SCOPED, "bounded retry"),
    "tinyassets/conversation_store.py::record_turn": (CALL_SCOPED, "bounded retry"),
    "tinyassets/credential_refresh.py::file_lock#2": (CALL_SCOPED, "lock acquisition"),
    "tinyassets/graph_compiler.py::_call_policy_router_with_retry": (
        CALL_SCOPED, "bounded retry",
    ),
    "tinyassets/memory/versioning.py::OutputVersionStore.save_draft": (
        CALL_SCOPED, "bounded retry",
    ),
    "tinyassets/process_liveness.py::hold_liveness": (CALL_SCOPED, "bounded retry"),
    "tinyassets/provider_assignment.py::_acquire_windows_file_lock": (
        CALL_SCOPED, "lock acquisition",
    ),
    "tinyassets/run_file_upload.py::StreamBridge.chunks#2": (CALL_SCOPED, "upload stream"),
    "tinyassets/run_file_upload.py::StreamBridge.push#2": (CALL_SCOPED, "upload stream"),
    "tinyassets/storage/automation_activations.py::AutomationActivationStore.connection": (
        CALL_SCOPED, "bounded retry",
    ),
    "tinyassets/storage/run_execution_lock.py::RunExecutionGuard._retire_uses#2": (
        CALL_SCOPED, "drain wait",
    ),
    "tinyassets/workspace_staging.py::_lock_tree_exclusive#2": (
        CALL_SCOPED, "lock acquisition",
    ),
    # -- should not exist in the target shape ----------------------------------
    "tinyassets/host_pool/bid_poller.py::BidPoller.run": (
        DELETE, "host-pool fleet client; no production importer (dark code)",
    ),
    "tinyassets/host_pool/heartbeat.py::HeartbeatLoop.run": (
        DELETE, "host-pool fleet heartbeat; no production importer (dark code)",
    ),
    "tinyassets/runtime/claimed_branch_execution.py::_continuous_heartbeat.beat": (
        DELETE, "fantasy_daemon claimed-task heartbeat; host-run daemon only",
    ),
    # -- the owner's own device -------------------------------------------------
    "tinyassets/desktop/tray.py::TrayApp._throttled_menu_refresh [Timer]": (
        CLIENT, "desktop tray menu refresh",
    ),
}

__all__ = ["BOX", "CALL_SCOPED", "CLASSES", "CLIENT", "CONTROL_PLANE", "DELETE", "SITES"]

# Generated facts are scaffolded by scripts/generate_guard_inventories.py;
# only the reviewed classification and rationale are maintained here.
SITES = CLASSIFICATION
