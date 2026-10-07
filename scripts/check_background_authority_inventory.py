"""Fail CI when a background Branch execution root escapes the reviewed inventory.

This checker is intentionally dark: it does not authorize execution or choose a
storage backend.  It makes two review obligations executable:

* every call to a Branch execution/queue boundary is an exact reviewed callsite;
* every source family and canonical read owner named by the OpenSpec target is
  still present at its recorded source marker.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True, order=True)
class CallSite:
    path: str
    function: str
    callee: str
    count: int = 1


@dataclass(frozen=True)
class SourceReference:
    path: str
    marker: str
    state: str = "implemented"


SENSITIVE_EXECUTION_CALLS = frozenset(
    {
        "_action_run_branch_version",
        "_execute_branch_core",
        "_run_fn",
        "append_task",
        "append_task_capped",
        "claim_task",
        "dispatch_selector",
        "execute_branch",
        "execute_branch_async",
        "execute_branch_version_async",
        "recover_expired",
        "recover_in_flight_runs",
        "resume_run",
        "stream",
    }
)

# Required reviewed CLASSIFICATION of canonical execution callsites.
# New boundaries must be reviewed; mirror paths are generated below.
CLASSIFICATION: tuple[CallSite, ...] = (
    # K1 remote MCP transport uses the governed connection effector stream, not
    # a graph executor. Exact launch/connection authority is rechecked per reply.
    CallSite("tinyassets/mcp_remote.py", "RemoteMcp._exchange", "self.stream"),
    # D2 Activities launches through the existing owner-bound run entrypoint.
    # Keep canonical and shipped copies exact; the activities root below records
    # the owner/provider checks, generation fence, and agent start barrier.
    CallSite("tinyassets/activity_runner.py", "start", "execute_branch_async"),
    # Reviewed 2026-08-26 while restoring this checker (PR #2561 deleted it as a
    # "dead script" -- it was not dead, it is the CI closure assertion for
    # `harden-background-branch-execution-authority`). All six were ALREADY
    # present and unregistered on main, so this test was red and non-gating:
    # `.github/heavy-test-files.txt` routes it to the `slow-tests` job, and only
    # `required-tests` is a required check. A guard that is red where nothing
    # reads it is the same failure as a green one that cannot go red. It is now
    # GREEN in that lane; promoting it to the required lane is a separate
    # two-line change, because editing `heavy-test-files.txt` triggers the
    # exact-head receipt gate and must not ride along with a 526-file cut.
    #
    # `enqueue_universe_branch_run` is a genuine background execution root and
    # documents itself as "the single audited path used by trigger sources that
    # carry their OWN authority (the inbound webhook token, an event-bus
    # subscription) rather than an MCP request identity" -- reviewed design that
    # was simply never registered here.
    # The two `stream` hits are NAME COLLISIONS, not execution boundaries: the
    # checker matches by callee name, and these are Starlette `request.stream()`
    # body reads -- both added by earlier Codex reviews specifically to bound an
    # HTTP body instead of buffering it whole. Neither touches a compiled graph
    # stream. Registered so the real graph-stream boundary stays detectable.
    # Reviewed 2026-10-01 (test-hygiene lane; the inventory test is heavy-listed,
    # so these entered unregistered and nothing failed a merge).
    # `recover_dead_owner_runs_now` (#4133) is the watcher/respawn twin of
    # `_ensure_runs_recovery`, and is covered by the same `recovery` root in
    # REQUIRED_BACKGROUND_ROOTS. It runs only in the process holding
    # `_RUNS_RECOVERY_LOCK`. It is NOT inert: started runs of a dead owner end
    # `interrupted` (never resumed), but never-started workspace waiters stay
    # queued and recovery kicks their dispatcher, which rebinds provider
    # authority from the stored owner; and terminal delivery emits
    # `run_completed`, which can wake another branch's automation under the
    # run's cause principal and that automation's owner (Codex 2026-10-01).
    # Tokenless legacy rows are judged by start time, not proven owner death.
    CallSite("tinyassets/api/runs.py", "recover_dead_owner_runs_now", "recover_in_flight_runs"),
    # Name collisions, like `_read_bounded_body` above: the billing webhook reads
    # its HTTP body through `request.stream()` to bound it (Codex 2026-08-28),
    # and hosted key exchange reads an httpx response with `client.stream()`.
    # Neither touches a compiled graph stream or grants execution authority.
    CallSite("tinyassets/onboarding/__init__.py", "_handle_billing_webhook", "request.stream"),
    CallSite("tinyassets/onboarding/hosted_model_auth.py", "exchange_key", "client.stream"),
    CallSite("tinyassets/onboarding/__init__.py", "_read_bounded_body", "request.stream"),
    CallSite(
        "tinyassets/universe_server.py",
        "create_streamable_http_app._hooks_endpoint",
        "request.stream",
    ),
    # Name collision (C1a, reviewed 2026-10-03): the dark stateless frontend
    # forwards the caller's HTTP body to the owner socket byte-for-byte via
    # Starlette `request.stream()`. It grants no execution authority; the
    # owner authenticates the forwarded request exactly as it would directly.
    CallSite("tinyassets/frontend.py", "Frontend.__call__", "request.stream"),
    # L7 durable ingress: bounded Starlette HTTP-body reads, not graph streams.
    # Acceptance grants no execution authority; register both shipped copies.
    CallSite("tinyassets/ingress.py", "AppIngressMiddleware.__call__", "request.stream"),
    CallSite(
        "fantasy_daemon/__main__.py",
        "DaemonController._try_execute_soul_loop",
        "execute_branch",
    ),
    CallSite("fantasy_daemon/__main__.py", "_try_dispatcher_pick", "claim_task"),
    # Reviewed 2026-08-05 (added by 64f27fe7, "Wire cloud drain epoch-2 Branch
    # consumer" #2182, without registration here). `recover_expired()` is
    # queue-owner maintenance over the canonical store: it grants no execution
    # authority. Every recovered task is filtered to this universe and to
    # `status == "pending"`, and must still pass the exact worker + activation
    # claim transaction below before anything executes.
    CallSite("fantasy_daemon/__main__.py", "_try_dispatcher_pick", "recover_expired"),
    # The assigned-queue consumer's `recover_expired()` call retired with its
    # epoch-2 claim pass (plan C2, 2026-09-28): it now runs only user-owned
    # automations and claims nothing from the queue.
    CallSite(
        "fantasy_daemon/__main__.py",
        "_try_execute_claimed_branch_task",
        "execute_branch",
    ),
    # The REAL compiled-graph stream boundary. Distinguishable from the two
    # `request.stream()` HTTP body reads only since receiver-sensitive matching
    # landed; before that all three were the bare name "stream".
    CallSite(
        "fantasy_daemon/__main__.py",
        "DaemonController._run_graph",
        "compiled.stream",
    ),
    # Reviewed 2026-10-02 (S7, change `control-plane-agent-loop`): the thin
    # loop's box tools READ the output of a box execution through
    # `BoxProvider.stream`, the box contract's output read (target architecture
    # D2). It starts nothing: the execution was started by `start_exec` on a
    # handle bound to the turn's own owner and command center, and runs no
    # branch. Registered for both the canonical module and its plugin mirror.
    CallSite("tinyassets/agent_loop/box_tools.py", "BoxExecutor._collect", "self.stream"),
    # Reviewed 2026-08-25 (run-provider-authority): server-owned trigger
    # enqueue enters the same governed foreground admission as the MCP action.
    # Reviewed 2026-08-29 (user-owned-automations 3.2). A due automation is a
    # background execution root, and it is registered here as one. It grants no
    # authority of its own: the provider call it hands in is the SAME foreground
    # session `enqueue_universe_branch_run` builds, differing only in that the
    # principal comes off the automation row (a consumer thread carries no
    # request identity). The async entry point is deliberate -- only it calls
    # `prepare_foreground_run_provider`, so the session admits against this run
    # id and re-derives the universe's current assignment and custody. Nothing
    # about the executor, provider or runtime is pinned anywhere on the path.
    CallSite(
        "tinyassets/automations.py",
        "_execute",
        "execute_branch_async",
    ),
    # `stream` is also Starlette's request-body API. These reviewed sites only
    # enforce bounded request bodies and grant no Branch execution authority.
    CallSite(
        "tinyassets/api/market.py",
        "_action_goal_run_canonical",
        "_action_run_branch_version",
    ),
    CallSite(
        "tinyassets/api/quality_leaderboard.py",
        "build_quality_leaderboard",
        "dispatch_selector",
    ),
    CallSite("tinyassets/api/runs.py", "_action_resume_run", "resume_run"),
    CallSite("tinyassets/api/runs.py", "_action_run_branch", "execute_branch_async"),
    CallSite(
        "tinyassets/api/runs.py",
        "enqueue_universe_branch_run",
        "execute_branch_async",
    ),
    CallSite(
        "tinyassets/api/runs.py",
        "_action_run_branch_version",
        "execute_branch_version_async",
    ),
    CallSite(
        "tinyassets/api/runs.py",
        "_ensure_runs_recovery",
        "recover_in_flight_runs",
    ),
    CallSite("tinyassets/api/universe.py", "_action_submit_request", "append_task"),
    CallSite(
        "tinyassets/dispatcher.py",
        "run_branch_task_producers_into_queue",
        "append_task",
    ),
    CallSite(
        "tinyassets/graph_compiler.py",
        "_build_invoke_branch_node._node_fn",
        "execute_branch",
    ),
    CallSite(
        "tinyassets/graph_compiler.py",
        "_build_invoke_branch_node._node_fn",
        "execute_branch_async",
    ),
    CallSite(
        "tinyassets/graph_compiler.py",
        "_build_invoke_branch_version_node._node_fn",
        "execute_branch_version_async",
        count=2,
    ),
    CallSite("tinyassets/runs.py", "execute_branch_async", "_execute_branch_core"),
    CallSite(
        "tinyassets/runs.py",
        "execute_branch_version_async",
        "_execute_branch_core",
    ),
    # Two calls, one subscription path (count 2 since 2026-10-01): the
    # principal-aware call forwards `event.owner_principal_id`; the
    # compatibility call forwards none. Both share the subscription's actor
    # mapping, revocation check and inputs. Production installs
    # `_inbound_event_run_fn`, which takes the principal and refuses a
    # non-universe actor or an empty principal, so the no-principal call is
    # reachable only with a test double (Codex 2026-10-01).
    CallSite(
        "tinyassets/scheduler.py",
        "Scheduler._dispatch_event",
        "_run_fn",
        count=2,
    ),
)


# Review the canonical authority boundary exactly once. The plugin builder
# copies tinyassets verbatim; its expected boundaries follow the same mapping.
# Scan BOTH trees below, so a missing/extra/changed mirror call still fails.
EXPECTED_SENSITIVE_CALL_SITES: tuple[CallSite, ...] = CLASSIFICATION + tuple(
    CallSite(
        "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/" + site.path,
        site.function, site.callee, site.count,
    )
    for site in CLASSIFICATION if site.path.startswith("tinyassets/")
)


REQUIRED_BACKGROUND_ROOTS: Mapping[str, tuple[SourceReference, ...]] = {
    "activities": (
        SourceReference(
            "tinyassets/activity_runner.py",
            "provider_call = _bind_automation_provider_call(base_path, who)",
        ),
        SourceReference(
            "tinyassets/activity_runner.py",
            "with owner_run_identity(base_path, universe_id, owner) as bound:",
        ),
        SourceReference(
            "tinyassets/activity_runner.py",
            "if not bound:\n            raise activities.ActivityRefused(",
        ),
        SourceReference("tinyassets/activity_runner.py", "owner_user_id=owner,"),
        SourceReference(
            "tinyassets/activity_runner.py", "on_node_status=_authority_guard(base_path, who),",
        ),
        SourceReference(
            "tinyassets/activity_runner.py",
            'if not activities.bind_run(universe_dir, record["activity_id"], generation, run_id):'
            "\n        stop(base_path, run_id)",
        ),
        SourceReference(
            "tinyassets/activity_runner.py",
            "record = activities.activity_for_run(universe_dir, run_id)",
        ),
        SourceReference(
            "tinyassets/activity_runner.py",
            'raise PermissionError(\n                "activity_run_unlinked:',
        ),
        SourceReference(
            "tinyassets/foreground_run_provider.py",
            "activity_runner.linked_activity(\n"
            "                    self._base_path, self._universe_id, self._run_id)\n"
            "                if activity_runner.is_activities_branch(\n"
            "                    self._branch_snapshot, self._universe_id)",
        ),
    ),
    "schedule_and_event": (
        SourceReference(
            "tinyassets/scheduler.py",
            'self._run_fn(sub["branch_def_id"], actor, inputs, run_name)',
        ),
    ),
    "goal_subscription": (
        SourceReference("tinyassets/subscriptions.py", "def subscribe("),
        SourceReference("tinyassets/scheduler.py", "def register_subscription("),
    ),
    "soul_and_compiled_cycle": (
        SourceReference(
            "fantasy_daemon/__main__.py",
            "read_legacy_premise(output_dir).strip()",
        ),
        SourceReference(
            "fantasy_daemon/__main__.py",
            "compiled.stream(initial_state, config=config)",
        ),
        SourceReference("fantasy_daemon/branches/universe_cycle.yaml", "graph_nodes:"),
    ),
    "request_admission": (
        SourceReference("tinyassets/storage/request_admissions.py", "class RequestAdmissionStore:"),
        SourceReference("tinyassets/api/universe.py", "def _action_submit_request("),
    ),
    "goal_pool_and_paid_market": (
        SourceReference("tinyassets/producers/goal_pool.py", "class GoalPoolProducer:"),
        SourceReference("tinyassets/producers/node_bid.py", "class NodeBidProducer:"),
    ),
    "branch_task_and_graph_enqueue": (
        SourceReference("tinyassets/branch_tasks.py", "def append_task("),
        SourceReference("tinyassets/graph_compiler.py", "def _node_enqueue_branch_run("),
    ),
    "direct_live_and_versioned_run": (
        SourceReference("tinyassets/runs.py", "def execute_branch_async("),
        SourceReference("tinyassets/runs.py", "def execute_branch_version_async("),
        SourceReference("tinyassets/runs.py", "def _execute_branch_core("),
    ),
    "resume_and_recovery": (
        SourceReference("tinyassets/runs.py", "def resume_run("),
        SourceReference("tinyassets/runs.py", "def recover_in_flight_runs("),
        SourceReference("tinyassets/branch_tasks.py", "def recover_claimed_tasks("),
        SourceReference("tinyassets/branch_tasks_v2.py", "    def recover_expired("),
    ),
    "request_actor_boundary": (
        SourceReference("tinyassets/api/permissions.py", "def current_request_actor_id("),
        SourceReference("tinyassets/api/engine_helpers.py", "def _current_actor("),
    ),
    "daemon_cloud_and_distributed_worker": (
        SourceReference("fantasy_daemon/__main__.py", "def _try_execute_claimed_branch_task("),
        # Retargeted 2026-08-29: the host-run `tinyassets/cloud_worker.py`
        # supervisor was deleted (nothing runs outside a user's universe --
        # ADR-009). The served consumer is now the only background claim root.
        SourceReference(
            "tinyassets/runtime/assigned_queue_consumer.py",
            "class AssignedQueueConsumer:",
        ),
        SourceReference("tinyassets/execution_authority/records.py", "class ExecutionGrantV1:"),
    ),
    "selector_leaderboard_and_market_delegate": (
        SourceReference("tinyassets/api/selector_dispatch.py", "def dispatch_selector("),
        SourceReference(
            "tinyassets/api/quality_leaderboard.py",
            "def recommend_parent_for_fork(",
        ),
        SourceReference("tinyassets/api/market.py", "def _action_goal_run_canonical("),
    ),
    "retired_wiki_forwarding": (
        # Hard Rule 15 (2026-09-24) deleted the enqueue helper; only the
        # legacy request-type constant for already-queued tasks remains.
        SourceReference(
            "tinyassets/bug_investigation.py",
            'REQUEST_TYPE_BUG_INVESTIGATION = "bug_investigation"',
            state="retirement-only",
        ),
        SourceReference(
            "tinyassets/api/wiki.py",
            "def _wiki_file_bug(",
            state="filing-only-negative",
        ),
        SourceReference(
            "packaging/claude-plugin/plugins/tinyassets-universe-server/"
            "runtime/tinyassets/api/wiki.py",
            "def _wiki_file_bug(",
            state="filing-only-negative",
        ),
    ),
}


CANONICAL_READ_INTERFACES: Mapping[str, tuple[SourceReference, ...]] = {
    "identity": (
        SourceReference("tinyassets/api/permissions.py", "def current_request_actor_id("),
    ),
    "acl": (SourceReference("tinyassets/api/permissions.py", "def universe_access_allows("),),
    "branch": (
        SourceReference("tinyassets/api/branches.py", "def _resolve_readable_branch("),
        SourceReference("tinyassets/api/branches.py", "def _resolve_readable_version("),
    ),
    "branch_write_author": (
        SourceReference("tinyassets/api/branches.py", "def _branch_authorized("),
    ),
    "daemon": (
        SourceReference("tinyassets/daemon_registry.py", "def get_daemon("),
        SourceReference("tinyassets/daemon_registry.py", "def list_runtime_instances("),
    ),
    "run": (
        SourceReference("tinyassets/api/runs.py", "def _run_read_allowed("),
        SourceReference("tinyassets/api/runs.py", "def _run_write_allowed("),
    ),
    "request_admission": (
        SourceReference("tinyassets/storage/request_admissions.py", "class RequestAdmissionStore:"),
    ),
    "filing_only_wiki_negative": (
        SourceReference("tinyassets/api/wiki.py", "def _wiki_file_bug("),
    ),
    "goal_subscription": (
        SourceReference("tinyassets/subscriptions.py", "def list_subscriptions("),
        SourceReference("tinyassets/scheduler.py", "def list_scheduler_subscriptions("),
    ),
    "queue": (
        SourceReference("tinyassets/branch_tasks.py", "def read_queue("),
        SourceReference("tinyassets/branch_tasks_v2.py", "class Epoch2OperationalRead:"),
    ),
    "b2": (
        SourceReference("tinyassets/execution_authority/records.py", "class ExecutionGrantV1:"),
        SourceReference(
            "tinyassets/execution_authority/records.py",
            "class RecordVerifier:",
            state="contract-only",
        ),
    ),
}


_SCAN_ROOTS = (
    "tinyassets",
    "fantasy_daemon",
    "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets",
)
_WIKI_NEGATIVE_PATHS = (
    "tinyassets/api/wiki.py",
    "packaging/claude-plugin/plugins/tinyassets-universe-server/runtime/tinyassets/api/wiki.py",
)
_WIKI_FORBIDDEN_CALLS = frozenset(
    {
        "_maybe_enqueue_investigation",
        "append_task",
        "append_task_capped",
        "enqueue_investigation_request",
        "execute_branch_async",
        "execute_branch_version_async",
    }
)


# Names generic enough that the RECEIVER decides whether the call is an
# execution boundary. `request.stream()` reads an HTTP body; `compiled.stream()`
# runs a graph. Recording only the attribute made them identical, so registering
# the two body reads as reviewed would have blinded the checker to a real
# compiled-graph stream added inside either function (cross-family review of
# PR #2561, round 6, mutation-proven).
RECEIVER_SENSITIVE = frozenset({"stream"})


def _callee_name(node: ast.Call) -> str | None:
    """The name to MATCH against the sensitive set (receiver discarded)."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _receiver_name(node: ast.Call) -> str:
    """The receiver expression's leading name, e.g. `request` in `request.stream()`."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return ""
    value = func.value
    while isinstance(value, ast.Attribute):
        value = value.value
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Call):
        inner = _callee_name(value)
        return f"{inner}()" if inner else "<expr>"
    return "<expr>"


def _recorded_callee(node: ast.Call, callee: str) -> str:
    """What goes in the manifest: qualified for receiver-sensitive names."""
    if callee not in RECEIVER_SENSITIVE:
        return callee
    receiver = _receiver_name(node)
    return f"{receiver}.{callee}" if receiver else callee


class _CallVisitor(ast.NodeVisitor):
    def __init__(self, names: frozenset[str]) -> None:
        self._names = names
        self._scope: list[str] = []
        self._aliases: dict[str, str] = {}
        self.calls: Counter[tuple[str, str]] = Counter()

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for imported in node.names:
            if imported.name in self._names:
                self._aliases[imported.asname or imported.name] = imported.name

    def _record_assigned_alias(self, target: ast.expr, value: ast.expr) -> None:
        if not isinstance(target, ast.Name):
            return
        canonical = None
        if isinstance(value, ast.Name):
            canonical = self._aliases.get(value.id, value.id)
        elif isinstance(value, ast.Attribute):
            canonical = value.attr
        if canonical in self._names:
            self._aliases[target.id] = canonical

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record_assigned_alias(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._record_assigned_alias(node.target, node.value)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        callee = _callee_name(node)
        if isinstance(node.func, ast.Name):
            callee = self._aliases.get(node.func.id, callee)
        if callee in self._names:
            recorded = _recorded_callee(node, callee)
            self.calls[(".".join(self._scope) or "<module>", recorded)] += 1
        self.generic_visit(node)


def scan_python_calls(
    path: Path,
    names: frozenset[str],
    *,
    relative_to: Path,
) -> set[CallSite]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = _CallVisitor(names)
    visitor.visit(tree)
    relative = path.relative_to(relative_to).as_posix()
    return {
        CallSite(path=relative, function=function, callee=callee, count=count)
        for (function, callee), count in visitor.calls.items()
    }


def collect_sensitive_call_sites(
    repo_root: Path,
    *,
    scan_roots: Sequence[str] = _SCAN_ROOTS,
) -> set[CallSite]:
    observed: set[CallSite] = set()
    for root_name in scan_roots:
        root = repo_root / root_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            observed.update(
                scan_python_calls(
                    path,
                    SENSITIVE_EXECUTION_CALLS,
                    relative_to=repo_root,
                )
            )
    return observed


def _all_references() -> Iterable[tuple[str, SourceReference]]:
    for group, refs in REQUIRED_BACKGROUND_ROOTS.items():
        for ref in refs:
            yield f"root:{group}", ref
    for group, refs in CANONICAL_READ_INTERFACES.items():
        for ref in refs:
            yield f"read:{group}", ref


def compare_call_sites(
    observed: Iterable[CallSite],
    expected: Iterable[CallSite],
) -> list[str]:
    observed_set = set(observed)
    expected_set = set(expected)
    errors = [
        f"unreviewed sensitive callsite: {callsite}"
        for callsite in sorted(observed_set - expected_set)
    ]
    errors.extend(
        f"stale sensitive callsite: {callsite}" for callsite in sorted(expected_set - observed_set)
    )
    return errors


def validate_inventory(repo_root: Path) -> list[str]:
    observed = collect_sensitive_call_sites(repo_root)
    errors = compare_call_sites(observed, EXPECTED_SENSITIVE_CALL_SITES)

    for owner, ref in _all_references():
        # An assertion against an ARCHIVED change is unfalsifiable: archives are
        # frozen, so the marker can never stop matching, while the canonical
        # requirement it stands in for is free to change or disappear. Four such
        # references existed after this PR archived their changes -- pointing at
        # text that could never fail while `openspec/specs/` carried no
        # equivalent. Removed, and refused here so none can return: if a
        # contract matters, it belongs in the canonical spec.
        # (Cross-family review of PR #2561, round 6.)
        if "openspec/changes/archive/" in ref.path:
            errors.append(
                f"{owner} references an ARCHIVED change, which can never fail: "
                f"{ref.path}. Point at openspec/specs/<capability>/spec.md, or "
                "drop the reference."
            )
            continue
        path = repo_root / ref.path
        if not path.is_file():
            errors.append(f"{owner} missing source: {ref.path}")
            continue
        text = path.read_text(encoding="utf-8")
        if ref.marker not in text:
            errors.append(f"{owner} missing marker in {ref.path}: {ref.marker!r}")

    for relative in _WIKI_NEGATIVE_PATHS:
        path = repo_root / relative
        for callsite in sorted(
            scan_python_calls(path, _WIKI_FORBIDDEN_CALLS, relative_to=repo_root)
        ):
            errors.append(f"filing-only wiki gained execution call: {callsite}")
    return errors


def _format_calls(calls: Sequence[CallSite]) -> str:
    return "\n".join(
        f"{call.path}::{call.function} -> {call.callee} [count={call.count}]"
        for call in sorted(calls)
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--print-observed", action="store_true")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    if args.print_observed:
        print(_format_calls(tuple(collect_sensitive_call_sites(root))))
        return 0
    errors = validate_inventory(root)
    if errors:
        print("\n".join(errors))
        return 1
    print("background authority inventory closure: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
