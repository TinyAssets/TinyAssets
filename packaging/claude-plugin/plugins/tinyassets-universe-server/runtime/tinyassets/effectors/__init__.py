"""External-write effectors — channel-agnostic dispatch.

Effectors translate ``external_write_packet``-shaped outputs from a node's
``output_keys`` into real-world side effects. They are NOT a new substrate
primitive type; they are glue that reads a documented packet shape out of a
run's final state and invokes a generic, credential-blind external call (or an
internal wiki write-back). Per the canonical 6+5 vocabulary, ``effects`` is a
``NodeDefinition`` attribute, not a fifth primitive. These functions are called
from the run-completion path in ``tinyassets.runs``; errors are captured into
the run's metadata, never raised to the user.

**The platform is channel-agnostic and ships with NO per-channel effector.** A
universe reaches any external service through the single generic
``authenticated_external_call`` sink over a user-configured connection — the
credential is applied inside an isolated worker process, never in this process.
Channels (GitHub, Slack, X, or anything not yet imagined) are user-built graph
nodes over that one primitive, not platform code. There is not a single channel
in the platform until a user builds one.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from tinyassets.effectors.authenticated_external_call import (
    EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
    bounded_evidence,
    packet_accept_statuses,
    packet_verb,
    run_authenticated_external_call_effector,
)
from tinyassets.effectors.wiki_write_back import (
    EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK,
    run_wiki_write_back_effector,
)
from tinyassets.effectors.workspace import (
    EXTERNAL_WRITE_SINK_WORKSPACE,
    WORKSPACE_READ_EFFECTS,
    is_cancellation,
    packet_op,
    run_workspace_effector,
)

#: How a held directory descriptor is spelled to a process that inherited it.
_PROC_FD_PREFIX = "/proc/self/fd/"


@dataclass
class AcquiredWorkspace:
    """One held use of a workspace capability.

    ``mount`` carries DUPLICATED descriptors: closing them is this holder's
    business alone, and the originals stay open until the chain says
    otherwise. Releasing twice is a no-op, because a double close would land
    on whatever fd number the runtime handed out next.
    """

    mount: Any
    _chain: Any = None
    _node_key: str = ""
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._chain is not None:
            self._chain._release_workspace(self._node_key, self.mount)
        else:  # pragma: no cover - a chainless acquisition is a test double
            _close_workspace_mount(self.mount)

    def __enter__(self) -> "AcquiredWorkspace":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()


def _dup_workspace_mount(mount: Any) -> Any:
    """A copy of *mount* holding ``os.dup``s of whatever descriptors it has.

    A mount with no descriptors (Windows, every test double) copies as itself:
    the path form has nothing to duplicate and nothing whose number could be
    reused. Anything DERIVED from a descriptor is re-derived from the dup, or
    the copy would hold this holder's fd while telling the jail to bind the
    original's number - which is the number a discard is about to free.

    Copied with ``copy.copy`` and assigned, not ``dataclasses.replace``: the
    capability is a dataclass today and a stub in three test suites, and a
    copier that only understands one of those silently hands back the ORIGINAL
    for the rest - which is the bug this function exists to prevent.
    """
    duplicated: dict[str, int] = {}
    try:
        for name in ("repo_fd", "lease_fd"):
            handle = getattr(mount, name, None)
            if isinstance(handle, int) and not isinstance(handle, bool):
                duplicated[name] = os.dup(handle)
    except OSError:
        _close_descriptors(duplicated.values())
        raise
    if not duplicated:
        return mount

    fields: dict[str, Any] = dict(duplicated)
    repo_dup = duplicated.get("repo_fd")
    if repo_dup is not None:
        source = str(getattr(mount, "bind_source", "") or "")
        if source.startswith(_PROC_FD_PREFIX):
            fields["bind_source"] = f"{_PROC_FD_PREFIX}{repo_dup}"
        if tuple(getattr(mount, "pass_fds", ()) or ()):
            fields["pass_fds"] = (repo_dup,)
    try:
        duplicate = copy.copy(mount)
        for name, value in fields.items():
            try:
                setattr(duplicate, name, value)
            except (AttributeError, TypeError):  # frozen dataclass
                object.__setattr__(duplicate, name, value)
    except Exception:
        # Never leak the dups when the copy itself fails - and never hand back
        # the original as if it were a copy.
        _close_descriptors(duplicated.values())
        raise
    return duplicate


def _close_descriptors(handles: Any) -> None:
    """Close raw descriptors, ignoring the ones already gone."""
    for handle in handles:
        if isinstance(handle, int) and not isinstance(handle, bool):
            try:
                os.close(handle)
            except OSError:
                pass


def _close_workspace_mount(mount: Any) -> None:
    """Close a mount's descriptors, whatever shape it is. Never raises."""
    closer = getattr(mount, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:  # noqa: BLE001 - teardown never fails a run
            logging.getLogger(__name__).exception("closing a workspace mount failed")


@dataclass
class WorkspaceMount:
    """One workspace this run holds, resolvable ONLY through the effect chain.

    A capability that can be named in state is a capability user text can
    forge, so a push or discard finds its workspace here -- by the node id of
    the checkout that created it -- and never through ``$ta.ref``.

    TWO descriptors, because they are two different directories and confusing
    them binds the wrong one:

    - ``repo_fd`` is ``<lease>/repo``, the repository itself. It is the BIND
      SOURCE, and ``bind_source``/``pass_fds`` are derived from it so
      ``/workspace`` IS the repository rather than a directory containing it.
    - ``lease_fd`` is the lease ROOT, one level up. Push reads the jail's
      export through it (``repo/.tiny-export/<sha>.bundle``), which is a path
      beneath the lease, not beneath the repo.

    The authority the checkout ran under is bound here too. A later push or
    discard DERIVES its destination and its connection from this object; a
    packet that names a different repository is refused rather than believed.
    """

    node_id: str
    bind_source: str
    lease_fd: Any = None
    lease: Any = None
    storage_class: str = "scratch"
    repo_key: str = ""
    generation: int = 0
    #: The repository directory's own descriptor, and what the jail binds.
    repo_fd: Any = None
    #: Carried to the sandbox unchanged: the child must inherit these.
    pass_fds: tuple[int, ...] = ()
    #: The authority this workspace was created under (Codex round 2, #6).
    host: str = ""
    repo: str = ""
    connection_id: str = ""
    grant_id: str = ""
    _closed: bool = False

    def close(self) -> None:
        """Close both descriptors exactly once. Idempotent.

        A capability that is revoked but whose descriptors stay open is a
        lease the outbox cannot reclaim, so the chain calls this on revoke and
        at settle, and the adapter calls it on every failure path after the
        mount was registered.
        """
        if self._closed:
            return
        self._closed = True
        import os as _os

        for descriptor in (self.repo_fd, self.lease_fd):
            if isinstance(descriptor, int) and not isinstance(descriptor, bool):
                try:
                    _os.close(descriptor)
                except OSError:
                    pass


def _authenticated_call_adapter(
    *, node_id, output_keys, run_state, base_path, run_id, dry_run,
    allowed_state_keys=None, prior_effects=None,
):
    return run_authenticated_external_call_effector(
        node_id=node_id,
        output_keys=output_keys,
        run_state=run_state,
        base_path=base_path,
        run_id=run_id,
        dry_run=dry_run,
        allowed_state_keys=allowed_state_keys,
        prior_effects=prior_effects,
    )


def _wiki_write_back_adapter(
    *, node_id, output_keys, run_state, base_path, run_id, dry_run, **_unused
):
    del dry_run  # internal write-back has no dry-run gate
    return run_wiki_write_back_effector(
        node_id=node_id,
        output_keys=output_keys,
        run_state=run_state,
        base_path=base_path,
        run_id=run_id,
    )


# Every external-write sink the platform knows -> its effector adapter. Only
# channel-agnostic sinks exist: the generic authenticated call and the internal
# wiki write-back. No GitHub/Slack/X/desktop sink lives here by design.
def _workspace_adapter(
    *, node_id, output_keys, run_state, base_path, run_id, dry_run,
    allowed_state_keys=None, prior_effects=None, ancestors=None, timeout_seconds=0.0,
    should_cancel=None,
):
    return run_workspace_effector(
        node_id=node_id,
        output_keys=output_keys,
        run_state=run_state,
        base_path=base_path,
        run_id=run_id,
        dry_run=dry_run,
        allowed_state_keys=allowed_state_keys,
        ancestors=ancestors,
        timeout_seconds=timeout_seconds,
        should_cancel=should_cancel,
    )


_EFFECTORS = {
    EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL: _authenticated_call_adapter,
    EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK: _wiki_write_back_adapter,
    EXTERNAL_WRITE_SINK_WORKSPACE: _workspace_adapter,
}


def _schema_defaulted_keys(state_schema) -> set:
    """The keys the compiler treats as declared for every node (BUG-085):
    state_schema entries carrying a default. Same helper the compiler uses."""
    try:
        from tinyassets.graph_compiler import _state_schema_defaults

        return set(_state_schema_defaults(state_schema).keys())
    except Exception:  # noqa: BLE001 - fail CLOSED: no extra keys become readable
        return set()


class EffectFailedError(Exception):
    """A node's declared effect could not do what its packet said (design D1,
    change `sandboxed-code-node`): the packet was refused before the wire, the
    adapter crashed, the sink does not exist, or the far side answered >= 400
    with a status the packet did not declare in ``accept_statuses`` (the HTTP
    method is not intent - a GraphQL query is a POST, a required GET that 404s
    must not feed an error body downstream). The node fails and the run ends
    ``failed`` with exactly the message shape the run surfaces already classify
    (``external write failed - <node>/<sink>: <error> [<kind>]``), so
    ``external_write_failed`` / ``external_write_refused`` keep their
    actionability. Later nodes do not run: no ``open_pr`` 422 after a refused
    ``write_readme``, no dangling branches (live 2026-08-30, runs
    6cb4d9f48a9949be / 7a7a91c14b0d4b8b)."""

    def __init__(self, node_id: str, sink: str, error: str, error_kind: str = "") -> None:
        self.node_id = node_id
        self.sink = sink
        self.error = error
        self.error_kind = error_kind or "effect_failed"
        super().__init__(
            f"external write failed - {node_id}/{sink}: {error} [{self.error_kind}]"
        )


_READ_VERBS = frozenset({"GET", "HEAD"})

#: There are NO per-run count budgets. ``RUN_DISPATCHES_MAX`` (5000 effect
#: dispatches), ``RUN_RPC_CALLS_MAX`` (5000 ``invoke_mcp_action`` round-trips) and
#: ``RUN_BYTES_MAX`` (256 MiB outbound) all refused a run partway through, telling
#: the author to "split the work across runs" -- a structural cap on what one run
#: may be. An account has exactly two limits, cloud bytes and concurrent agent
#: seats (founder, 2026-09-30), and a run is bounded by holding a seat for as long
#: as it takes, not by a count of what it did with it.
#:
#: The counters themselves stay: ``dispatches``, ``rpc_calls`` and ``bytes_out``
#: on the chain are what a receipt and a status read report, and an observation is
#: not a limit. Per-CALL payload bounds stay too -- those validate one request.
#:
#: Charged when a delivered call did not report its sizes: the per-call bounds.
_UNKNOWN_REQUEST_BYTES = 8 * 1024 * 1024
_UNKNOWN_RESPONSE_BYTES = 5 * 1024 * 1024


def _bytes_moved(per_node: dict) -> int:
    total = 0
    for result in (per_node or {}).values():
        if not isinstance(result, dict) or result.get("delivered") is not True:
            continue
        req = result.get("request_bytes")
        resp = result.get("response_bytes")
        total += int(req) if isinstance(req, int) else _UNKNOWN_REQUEST_BYTES
        total += int(resp) if isinstance(resp, int) else _UNKNOWN_RESPONSE_BYTES
    return total


@dataclass
class EffectChain:
    """Run-scoped effect state. Effects fire at node time (design D1); this is
    what they leave behind for the rest of the run and for persistence:

    - ``results``: node_id -> the FULL authenticated-call result, in memory
      only, so a later packet's ``$ta.effect`` and a code node's ``effects``
      see whole bodies, never the 4 KiB preview.
    - ``evidence``: node_id -> {sink: bounded evidence} - what is persisted on
      every terminal status, failure included.
    - ``fired``: one (sink, verb) per effect that ran - what the engine budget
      settles on (``tinyassets.engine_admissions``).

    The chain is created by the runner before ``compile_branch``, registered
    under its run id so ``update_run_status`` can settle from ``fired`` on any
    terminal path, and forgotten at terminal status.
    """

    run_id: str = ""
    base_path: Any = None
    dry_run: bool | None = None
    cloud_effect_session: Any = None
    results: dict[str, dict] = field(default_factory=dict)
    evidence: dict[str, dict] = field(default_factory=dict)
    fired: list[tuple[str, str | None]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    #: Dispatch accounting (Codex rounds 2-3): ``inflight`` reserves a key
    #: before its adapter runs (at most once, race-free); ``active`` counts
    #: adapters running right now so ``settle`` can WAIT for them without
    #: serialising unrelated effects; ``dispatching`` holds the thread idents
    #: inside a dispatch so a same-thread settle defers instead of deadlocking
    #: or settling early; a dispatch that ends after settlement re-settles
    #: (write is final, so a late write is never lost).
    inflight: set[str] = field(default_factory=set)
    active: int = 0
    dispatching: set[int] = field(default_factory=set, repr=False)
    settle_pending: bool = False
    closed: bool = False
    settled: bool = False
    #: Effects that fired in an EARLIER segment of this run (before an
    #: interrupt), seeded from the persisted output on resume so "at most once
    #: per run" survives the resume (Codex round 3, P0).
    already_fired: set[str] = field(default_factory=set)
    #: ``invoke_mcp_action`` round-trips this RUN has made (the child's own cap
    #: is per node, so the run-wide bound lives here - Codex round 2, P1);
    #: persisted on interrupt and seeded on resume.
    rpc_calls: int = 0
    invocation_depth: int = 0
    #: The run's own provider call, for the auto-review of a consequential
    #: action (harness D1d), and whether this chain belongs to a runner at all.
    #: The legacy post-run dispatcher has no runner and no run model.
    review_provider: Any = field(default=None, repr=False)
    review_active: bool = False
    #: Usage budgets (change `run-usage-budgets`): what this RUN has dispatched
    #: and moved; the hourly half is in the admissions ledger under
    #: ``universe_id``. Graph shape is unbounded; this is what bounds it.
    universe_id: str = ""
    dispatches: int = 0
    bytes_out: int = 0
    #: Workspace capabilities this RUN may bind, keyed by the checkout node
    #: that delivered each one (design D2). In memory only and never
    #: serialised: a branch resolves a workspace by naming an ancestor
    #: checkout, never by carrying a lease id through state or ``$ta.ref``.
    #: A workspace is never shared across runs or universes, and a capability
    #: that outlived its run would outlive the lease that backs it. Workspace
    #: bytes are the pool ledger's, NOT ``bytes_out``: the HTTP usage budget
    #: bounds outbound calls only (D4).
    workspaces: dict[str, Any] = field(default_factory=dict)

    #: Live acquisitions per node key. A capability with holders outstanding
    #: is not closed on revoke: the fd NUMBER would be reused by the next
    #: checkout and a node still holding it would read another branch's
    #: repository (Codex code round 3, P0 #2).
    workspace_holds: dict[str, int] = field(default_factory=dict)
    #: Revoked mounts whose original descriptors are owed a close once the
    #: last holder releases.
    workspace_closing: dict[str, Any] = field(default_factory=dict)

    def acquire_workspace(self, node_key: str) -> "AcquiredWorkspace | None":
        """Take a capability for the length of ONE use, or None.

        None means the same thing ``workspace_mount`` returning None means -
        never delivered, or revoked - and it is decided INSIDE the lock, so an
        acquisition cannot straddle a revoke.

        What the caller gets is not the registry's mount: it is a copy holding
        ``os.dup``s of the descriptors. That is the whole point. A parallel
        ``discard`` closes the originals, the next checkout opens directories
        and gets the SAME fd numbers back, and a holder still using them would
        be reading another branch's repository. A dup is a separate reference
        to the same open file description: the number cannot be reused while it
        is held, and closing it costs the original nothing.

        Use it as a context manager, or call ``release()`` in a ``finally``.
        """
        key = str(node_key)
        with self.lock:
            mount = self.workspaces.get(key)
            if mount is None:
                return None
            duplicate = _dup_workspace_mount(mount)
            self.workspace_holds[key] = self.workspace_holds.get(key, 0) + 1
        return AcquiredWorkspace(mount=duplicate, _chain=self, _node_key=key)

    def _release_workspace(self, node_key: str, duplicate: Any) -> None:
        """Close this holder's dups and, if it was the last one, whatever the
        revoke deferred."""
        _close_workspace_mount(duplicate)
        with self.lock:
            remaining = self.workspace_holds.get(node_key, 0) - 1
            if remaining > 0:
                self.workspace_holds[node_key] = remaining
                return
            self.workspace_holds.pop(node_key, None)
            deferred = self.workspace_closing.pop(node_key, None)
        _close_workspace_mount(deferred)

    def register_workspace(self, node_key: str, mount: Any) -> None:
        """Publish the generation a checkout node delivered, for this run only."""
        if not isinstance(node_key, str) or not node_key.strip():
            raise ValueError("register_workspace needs a node key")
        if mount is None:
            raise ValueError(
                f"register_workspace({node_key!r}) needs a mount, not None"
            )
        with self.lock:
            self.workspaces[node_key] = mount

    def workspace_mount(self, node_key: str) -> Any:
        """The mount *node_key* delivered, or None.

        Absent covers both halves of the same fact: the checkout never ran
        or never delivered, and a ``discard`` revoked it. The registry
        answers the question; what to do about "absent" belongs to the
        caller - the compiler fails the node by name, the adapter returns a
        structured refusal.
        """
        with self.lock:
            return self.workspaces.get(str(node_key))

    # Same answer under the name the adapter uses.
    workspace_mount_or_none = workspace_mount

    def revoke_workspace(self, node_key: str) -> Any:
        """Drop the capability: a later ``ws`` node in this run refuses.
        Idempotent; returns what was dropped (None if nothing). The mount's
        descriptors are closed here - a capability object that outlives the
        run would keep the lease directory open for the daemon's lifetime
        (Codex code round 2, #7)."""
        key = str(node_key)
        with self.lock:
            mount = self.workspaces.pop(key, None)
            # Revoked IMMEDIATELY - a later acquisition gets None either way -
            # but the descriptors stay open while anyone holds a dup, because
            # closing them frees the NUMBER for the next checkout to reuse.
            deferred = mount is not None and self.workspace_holds.get(key, 0) > 0
            if deferred:
                self.workspace_closing[key] = mount
        if not deferred:
            _close_workspace_mount(mount)
        return mount

    def close_workspaces(self) -> int:
        """Revoke and close every workspace this run still holds. Called
        when the chain settles; idempotent."""
        with self.lock:
            held = dict(self.workspaces)
            self.workspaces.clear()
            closable = []
            for key, mount in held.items():
                if self.workspace_holds.get(key, 0) > 0:
                    # Same rule as revoke: a run that settles while a node is
                    # still inside its workspace must not free the fd numbers
                    # under it. The last release closes them.
                    self.workspace_closing[key] = mount
                else:
                    closable.append(mount)
        for mount in closable:
            _close_workspace_mount(mount)
        return len(held)

    def prior_effects(self, ancestors: set[str] | None = None) -> dict[str, dict]:
        """Full results of the nodes a reference may legally name (for
        ``$ta.effect``): the node's graph ANCESTORS (Codex round 1, P1) - a
        graph-defined relation, not "whatever happened to complete first",
        so the same branch never resolves on one run and refuses on the next.
        ``None`` = no ancestry known (legacy post-run dispatch): every
        completed node."""
        with self.lock:
            items = dict(self.results)
        if ancestors is None:
            return items
        return {k: v for k, v in items.items() if k in ancestors}

    def effects_view(self, ancestors: set[str] | None = None) -> dict[str, dict]:
        """What a code node receives as ``effects``: node_id -> {status, body}
        for its graph ancestors, a JSON body parsed when it parses. NEVER the
        response headers: ``$ta.effect`` denies them and persisted evidence
        strips their values because a ``Set-Cookie`` there is a credential
        (Codex round 1, P0) - code gets exactly what a packet could reference,
        no more."""
        out: dict[str, dict] = {}
        for node_id, result in self.prior_effects(ancestors).items():
            response = result.get("response") if isinstance(result.get("response"), dict) else None
            if not isinstance(response, dict):
                continue
            body = response.get("body")
            if isinstance(body, str) and body.lstrip()[:1] in ("{", "["):
                try:
                    body = json.loads(body)
                except ValueError:
                    pass
            out[node_id] = {"status": response.get("status"), "body": body}
        return out

    def delivered_nodes(self) -> list[str]:
        """Nodes whose effect reached the far side - what a failed run must
        still report (design D1: 'failed after writes' is a real state)."""
        with self.lock:
            evidence = dict(self.evidence)
        out = []
        for node_id, per_sink in evidence.items():
            for result in (per_sink or {}).values():
                if isinstance(result, dict) and result.get("delivered") is True:
                    out.append(node_id)
                    break
        return out

    #: How long a terminal status waits for adapters still running before it
    #: settles with what has fired so far. A dispatch that finishes later
    #: re-settles; since a write settlement is final, nothing is lost - the
    #: wait only makes the common case exact.
    SETTLE_WAIT_S = 30.0

    def settle(self) -> None:
        """Close the chain (nothing may fire after a terminal status) and
        settle the run's engine admission from what fired. Waits for adapters
        still running - except when called from inside one of them on the
        same thread, where it defers to that dispatch's end instead of
        settling an incomplete list (Codex round 3, P0). Idempotent; a
        dispatch that ends after settlement re-settles, and a write settlement
        is final."""
        import time as _time

        with self.lock:
            self.closed = True
            if threading.get_ident() in self.dispatching:
                self.settle_pending = True
                return
            deadline = _time.monotonic() + self.SETTLE_WAIT_S
            while self.active > 0:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    logging.getLogger(__name__).warning(
                        "effect chain %s: settling with %d adapter(s) still running "
                        "after %.0fs; a late write re-settles as a write",
                        self.run_id, self.active, self.SETTLE_WAIT_S,
                    )
                    break
                self._cond.wait(remaining)
        self._settle_now()

    def _settle_now(self) -> None:
        with self.lock:
            fired = list(self.fired)
            self.settled = True
            self.settle_pending = False
        # The run is over: nothing may hold a lease's descriptors open past
        # here, or the outbox cannot wipe what it is owed. BEFORE the
        # settlement, so a raise in there cannot skip the close.
        self.close_workspaces()
        settle_engine_admission(self.run_id, fired)

    @property
    def _cond(self) -> threading.Condition:
        cond = self.__dict__.get("_cond_obj")
        if cond is None:
            cond = threading.Condition(self.lock)
            self.__dict__["_cond_obj"] = cond
        return cond

    def seed_from_output(self, output: Any) -> None:
        """On resume: what the interrupted segment already fired and spent,
        so the run-wide bounds hold across the interrupt (Codex round 3,
        P0)."""
        if not isinstance(output, dict):
            return
        fired = output.get("external_write_results")
        before = output.get("effects_fired_before")
        with self.lock:
            if isinstance(fired, dict):
                self.already_fired.update(str(k) for k in fired)
            if isinstance(before, list):
                self.already_fired.update(str(k) for k in before)
        try:
            self.rpc_calls = max(self.rpc_calls, int(output.get("rpc_calls") or 0))
        except (TypeError, ValueError):
            pass
        try:
            self.invocation_depth = int(output.get("invocation_depth") or self.invocation_depth)
        except (TypeError, ValueError):
            pass

    def rpc_permit(self) -> None:
        """Count one ``invoke_mcp_action`` round-trip. Counts; never refuses.

        There is no cap to permit against any more, so the name is now about the
        counting. A run is bounded by its seat, and each individual call is still
        bounded by its own payload limits.
        """
        with self.lock:
            self.rpc_calls += 1


_ACTIVE_CHAINS: dict[str, EffectChain] = {}
_ACTIVE_CHAINS_LOCK = threading.Lock()


def register_effect_chain(chain: EffectChain) -> None:
    if not chain.run_id:
        return
    with _ACTIVE_CHAINS_LOCK:
        _ACTIVE_CHAINS[chain.run_id] = chain


def active_effect_chain(run_id: str) -> EffectChain | None:
    with _ACTIVE_CHAINS_LOCK:
        return _ACTIVE_CHAINS.get(run_id)


def forget_effect_chain(run_id: str) -> EffectChain | None:
    with _ACTIVE_CHAINS_LOCK:
        return _ACTIVE_CHAINS.pop(run_id, None)


def _close_workspace_mount(mount: Any) -> None:
    """Close a mount's descriptors exactly once; a mount without ``close`` is
    a test double. Never raises - the run is already past the point where a
    close failure could change its outcome, so it is logged."""
    if mount is None:
        return
    closer = getattr(mount, "close", None)
    if not callable(closer):
        return
    try:
        closer()
    except Exception:  # noqa: BLE001 - logged, never fatal at settle
        logging.getLogger(__name__).exception("workspace mount close failed")


def dispatch_node_effects(
    chain: EffectChain,
    node,
    run_state: dict,
    *,
    state_schema=None,
    ancestors: set[str] | None = None,
    node_key: str | None = None,
    should_cancel=None,
) -> dict[str, dict]:
    """Fire ``node.effects`` NOW, against ``run_state`` = the state the node saw
    merged with the delta it returned (the packet lives in that delta). Records
    full results, bounded evidence and fired verbs on ``chain`` under
    ``node_key`` (the GRAPH node id - two graph nodes sharing one definition
    are two effects, Codex round 2); raises ``EffectFailedError`` per design
    D1 so the node - and the run - fail.

    A node's effects fire AT MOST ONCE per run (Codex round 1, P0), reserved
    under the lock BEFORE the adapter runs so two concurrent visits cannot
    both fire (round 2, P0); nothing fires after the chain closed."""
    effects = list(getattr(node, "effects", None) or [])
    if not effects:
        return {}
    key = node_key or getattr(node, "node_id", "")
    me = threading.get_ident()
    with chain.lock:
        if chain.closed:
            raise EffectFailedError(
                key, effects[0],
                "the run already reached a terminal status; no effect may fire after it",
                "run_closed",
            )
        if key in chain.evidence or key in chain.inflight or key in chain.already_fired:
            raise EffectFailedError(
                key, effects[0],
                "this node's effects already fired in this run - a cycle revisited it "
                "(or the run resumed past it); each node's effects fire at most once per "
                "run, so route the loop around the effect node or split the branch",
                "effect_already_fired",
            )
        chain.inflight.add(key)
        chain.active += 1
        chain.dispatching.add(me)
        chain.dispatches += 1
    resettle = False
    try:
        per_node = _fire_node_effects(
            node, run_state, chain=chain,
            schema_defaulted=_schema_defaulted_keys(state_schema),
            ancestors=ancestors, node_key=key,
            should_cancel=should_cancel,
        )
        accept = packet_accept_statuses(
            output_keys=list(getattr(node, "output_keys", None) or []),
            run_state=run_state,
        )
        _mark_accepted_statuses(per_node, accept)
        moved = _bytes_moved(per_node)
        with chain.lock:
            chain.evidence[key] = per_node
            chain.bytes_out += moved
    finally:
        with chain.lock:
            chain.inflight.discard(key)
            chain.active -= 1
            chain.dispatching.discard(me)
            # A settle that arrived while we ran: either it deferred to us
            # (same thread) or it gave up waiting - settle again now with the
            # complete list; a write settlement is final, so this only adds.
            resettle = chain.closed and (chain.settle_pending or chain.settled)
            chain._cond.notify_all()
        if resettle:
            chain._settle_now()
    failure = first_effect_failure(per_node, accept_statuses=accept)
    if failure is not None:
        sink, error, kind = failure
        raise EffectFailedError(key, sink, error, kind)
    return per_node


def _mark_accepted_statuses(per_node: dict, accept: set[int]) -> None:
    """A delivered call answered >= 400 with a status the packet declared
    acceptable is data: mark the evidence row so the completion path does not
    re-report it as an external-write error (Codex round 2, P1)."""
    for result in (per_node or {}).values():
        if not isinstance(result, dict) or result.get("delivered") is not True:
            continue
        response = result.get("response") if isinstance(result.get("response"), dict) else {}
        status = response.get("status")
        if isinstance(status, int) and status >= 400 and status in accept:
            result["accepted_status"] = True


def first_effect_failure(
    per_node: dict, *, accept_statuses: set[int] | None = None,
) -> tuple[str, str, str] | None:
    """The D1 rule over one node's evidence: (sink, error, kind) for the first
    effect that failed the node, or None. A refusal before the wire, a crash
    and a dead sink always fail. A delivered call answered >= 400 fails
    whatever its verb - the HTTP method is not intent (a GraphQL query is a
    POST; a required GET that 404s must not feed an error body downstream,
    Codex round 1) - UNLESS the packet declared that status in
    ``accept_statuses`` (probe-then-branch: ``"accept_statuses": [404]``).
    Accounting (read vs write for the budget) stays verb-based and separate."""
    accept = set(accept_statuses or ())
    for sink, result in (per_node or {}).items():
        if not isinstance(result, dict):
            continue
        if result.get("delivered") is True:
            response = result.get("response") if isinstance(result.get("response"), dict) else {}
            status = response.get("status")
            if isinstance(status, int) and status >= 400 and status not in accept:
                body = response.get("body")
                preview = (body if isinstance(body, str) else str(body or ""))[:200]
                error = f"far side answered HTTP {status}: {preview}".rstrip(": ")
                return (sink, error, "far_side_error")
            continue
        kind = str(result.get("error_kind") or "")
        error = str(result.get("error") or "")
        if error or kind:
            return (sink, error or f"refused before the wire: {kind}", kind or "effect_failed")
    return None


def _fire_node_effects(
    node, run_state, *, chain: EffectChain, schema_defaulted: set,
    ancestors: set[str] | None = None, node_key: str | None = None,
    should_cancel=None,
) -> dict:
    """Run every sink one node declares and return its bounded evidence
    ({sink: result}); full authenticated-call results and fired verbs land on
    ``chain`` under ``node_key``. Failures are structured rows (the caller applies
    D1); owner cancellation propagates without dispatching later sinks."""
    node_id = node_key or getattr(node, "node_id", "")
    output_keys = list(getattr(node, "output_keys", None) or [])
    # A packet may read ONLY the node's declared input_keys plus the
    # state_schema-defaulted keys - never the whole final state (Codex
    # round 1, P0). This is NARROWER than the compiler's render view
    # (which shows everything when isolation is off or no inputs are
    # declared) on purpose: an effect reads less, never more.
    allowed_state_keys = set(getattr(node, "input_keys", None) or []) | schema_defaulted
    prior_effects = chain.prior_effects(ancestors)
    per_node: dict[str, dict] = {}
    for sink in list(getattr(node, "effects", None) or []):
        adapter = _EFFECTORS.get(sink)
        if adapter is None:
            # Say what to do instead, not just what is wrong. A branch stored
            # before a per-channel sink was retired keeps naming it forever,
            # and the node then does NOTHING every run. Observed live
            # 2026-08-29: a stored "Docs Touch PR" branch still declared
            # `github_pull_request`. The platform ships exactly two sinks on
            # purpose (channels are user-built graph nodes over the generic
            # call, never hard-coded effectors): the branch must be rebuilt
            # against a supported sink.
            supported = ", ".join(sorted(_EFFECTORS))
            # A sink we do not know is not a read (Codex round 2): what it
            # would have done is unknown, so the run stays a write.
            with chain.lock:
                chain.fired.append((sink, None))
            per_node[sink] = {
                "error": (
                    f"unknown effect sink: {sink} - this branch names a sink "
                    f"that no longer exists, so this node does nothing. "
                    f"Rebuild the node against one of: {supported}. For "
                    f"outbound HTTP, that is "
                    f"'{EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL}' with an "
                    f"external_write_packet in its output_keys."
                ),
                "error_kind": "unknown_sink",
                "supported_sinks": sorted(_EFFECTORS),
            }
            continue
        try:
            adapter_kwargs = dict(
                node_id=node_id,
                output_keys=output_keys,
                run_state=run_state,
                base_path=chain.base_path,
                run_id=chain.run_id,
                dry_run=bool(chain.dry_run),
                allowed_state_keys=allowed_state_keys,
                prior_effects=prior_effects,
            )
            if sink == EXTERNAL_WRITE_SINK_WORKSPACE:
                # Workspace capabilities follow graph ancestry, not HTTP-result
                # membership: workspace ancestors produce no HTTP response.
                adapter_kwargs["ancestors"] = ancestors
                # A server-owned closure over the actual root run database,
                # not a packet field or a query against workspace storage.
                adapter_kwargs["should_cancel"] = should_cancel
                adapter_kwargs["timeout_seconds"] = float(
                    getattr(node, "timeout_seconds", 0.0) or 0.0
                )
            from tinyassets.agent_review import bound as review_bound

            with review_bound(chain.review_provider, active=chain.review_active,
                              run_id=chain.run_id):
                result = adapter(**adapter_kwargs)
        except Exception as exc:  # defensive: never raise from an adapter
            if is_cancellation(exc):
                # The owner stopped the run. Recording it as one node's crash
                # would leave the run reported as broken rather than cancelled,
                # and would let the remaining effects fire behind the stop.
                raise
            result = {
                "error": f"effector crashed: {exc}",
                "error_kind": "effector_crashed",
            }
        if not chain.dry_run:
            verb = result.get("verb") if isinstance(result, dict) else None
            if isinstance(result, dict) and result.get("error_kind") == "method_mismatch":
                # verb and request.method disagreed: the result echoes the
                # declared verb, which says nothing about intent (Codex).
                verb = None
            elif not verb and sink == EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL:
                # Refused before the wire (a gate, a bad packet): the verb the
                # packet DECLARED still says whether it could have written.
                verb = packet_verb(output_keys=output_keys, run_state=run_state)
            elif not verb and sink == EXTERNAL_WRITE_SINK_WORKSPACE:
                # Same rule for the workspace sink: the op the packet declared
                # is what says whether the far side could have changed. An op
                # we cannot read stays None, which settles as a write.
                verb = packet_op(output_keys=output_keys, run_state=run_state)
            with chain.lock:
                chain.fired.append((sink, verb))
        if sink == EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL and isinstance(result, dict):
            with chain.lock:
                chain.results[node_id] = result
            _log_effect_not_ok(chain.run_id, node_id, result)
            result = bounded_evidence(result, node_id=node_id)
        per_node[sink] = result
    return per_node


def run_effects_for_branch(
    *,
    branch,
    run_state,
    base_path=None,
    run_id="",
    dry_run=None,
    cloud_effect_session=None,
    review_provider=None,
):
    """Post-run dispatch of every node's effects, in branch STORAGE order.

    This is the legacy path for a branch compiled WITHOUT an effect chain
    (tests, callers outside the runner). Production runs fire effects at node
    time through ``dispatch_node_effects`` and never come here - the runner
    reads the chain's evidence instead, so nothing is dispatched twice.
    Failures are structured rows, never raised.

    A consequential action is checked first (harness D1d) only with an
    explicit ``review_provider``; without one it is held, never sent.
    """
    chain = EffectChain(
        run_id=run_id, base_path=base_path, dry_run=dry_run,
        cloud_effect_session=cloud_effect_session,
        review_provider=review_provider, review_active=review_provider is not None,
    )
    schema_defaulted = _schema_defaulted_keys(getattr(branch, "state_schema", None))
    node_defs = list(getattr(branch, "node_defs", None) or [])
    by_id = {getattr(n, "node_id", ""): n for n in node_defs}
    graph_nodes = list(getattr(branch, "graph_nodes", None) or [])
    # One effect per GRAPH node (two graph nodes over one definition are two
    # effects - Codex round 3), in storage order; a branch without graph
    # nodes (tests) falls back to its definitions.
    pairs = (
        [(gn.id, by_id.get(gn.node_def_id or gn.id)) for gn in graph_nodes]
        if graph_nodes else [(getattr(n, "node_id", ""), n) for n in node_defs]
    )
    for key, node in pairs:
        if node is None or not list(getattr(node, "effects", None) or []):
            continue
        chain.evidence[key] = _fire_node_effects(
            node, run_state, chain=chain, schema_defaulted=schema_defaulted, node_key=key,
        )
    chain.settle()
    return chain.evidence

def _log_effect_not_ok(run_id, node_id, result) -> None:
    """One log line per outbound call that did not succeed: the far side
    answered >= 400, or the packet was refused before the wire. Concern
    2026-08-28: GitHub said 403 and the daemon log said nothing for 25 minutes
    while the reason sat in the evidence map. Status line and the first bytes
    of the response only - never the request, which carries the credential
    path. Never raises into the completion path."""
    try:
        response = result.get("response")
        status = response.get("status") if isinstance(response, dict) else None
        if isinstance(status, int) and status >= 400:
            body = response.get("body")
            preview = (body if isinstance(body, str) else str(body or ""))[:200]
            logging.getLogger(__name__).warning(
                "external effect refused by the far side: run=%s node=%s status=%s body=%s",
                run_id,
                node_id,
                status,
                preview.replace(chr(10), " "),
            )
        elif result.get("error") or result.get("error_kind"):
            logging.getLogger(__name__).warning(
                "external effect did not fire: run=%s node=%s kind=%s error=%s",
                run_id,
                node_id,
                result.get("error_kind"),
                str(result.get("error") or "")[:200].replace(chr(10), " "),
            )
    except Exception:  # pragma: no cover - defensive: the completion path never raises
        pass


def settle_engine_admission(run_id, fired) -> None:
    """Downgrade the run's engine admission to a read when nothing it fired
    could have changed the far side (GET/HEAD authenticated calls, or nothing
    at all - a run that failed or was cancelled fired nothing, because effects
    fire only after success). A run that was not engine-triggered has no
    admission row: no-op. Never raises into the completion path; a failure to
    settle leaves the row a write, which is the strict side."""
    if not run_id:
        return
    try:
        from tinyassets.engine_admissions import fired_only_reads, reclassify_read, settle_write

        if fired_only_reads(
            list(fired),
            read_sink=EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
            read_effects=WORKSPACE_READ_EFFECTS,
        ):
            reclassify_read(str(run_id))
        else:
            # Final: a status rewritten to FAILED after these effects fired
            # must not turn this write into a read (Codex round 3).
            settle_write(str(run_id))
    except Exception:  # pragma: no cover - defensive: the completion path never raises
        logging.getLogger(__name__).exception("engine admission settle failed")


__all__ = [
    "EffectChain",
    "EffectFailedError",
    "dispatch_node_effects",
    "first_effect_failure",
    "register_effect_chain",
    "active_effect_chain",
    "forget_effect_chain",
    "EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL",
    "EXTERNAL_WRITE_SINK_WIKI_WRITE_BACK",
    "EXTERNAL_WRITE_SINK_WORKSPACE",
    "WORKSPACE_READ_EFFECTS",
    "WorkspaceMount",
    "run_authenticated_external_call_effector",
    "run_wiki_write_back_effector",
    "run_workspace_effector",
    "run_effects_for_branch",
]
