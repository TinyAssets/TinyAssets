"""Process startup state for a daemon inside its one owner's box."""

from __future__ import annotations

import os
import socket
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tinyassets import rpc_frames as rf


@dataclass(frozen=True)
class OwnerRuntime:
    principal: str
    uid: int
    root: Path
    pid: int
    tools: object
    relay: Path | None = None


_runtime: OwnerRuntime | None = None


def current() -> OwnerRuntime | None:
    if _runtime is not None and _runtime.pid != os.getpid():
        raise PermissionError("owner daemon admission requires a fresh process startup")
    return _runtime


def start(data: Path, relay: Path) -> OwnerRuntime:
    """Read identity once from the per-box socket, before constructing the app."""
    global _runtime
    if _runtime is not None:
        raise RuntimeError("owner runtime is already initialized")
    # A misconfigured launcher must fail before constructing any owner app.
    # Values are never included in the error. The image's static environment
    # and the null netns remain boxhostd's startup contract.
    if any(value and (name.upper().endswith(("_API_KEY", "_ACCESS_TOKEN", "_REFRESH_TOKEN",
                                             "_SECRET_KEY", "_CLIENT_SECRET"))
                      or name.upper() in {"ANTHROPIC_AUTH_TOKEN", "STRIPE_SECRET",
                                          "OPENAI_API_KEY", "ANTHROPIC_API_KEY"})
           for name, value in os.environ.items()):
        raise PermissionError("owner daemon environment contains a raw credential")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(5)
        channel.connect(str(relay))
        channel.sendall(rf.control(rf.CONNECTION, {"op": "WHOAMI"}))
        answer = rf.read_frame_blocking(channel)
    if answer is None or answer.kind != rf.CONTROL or answer.stream != rf.CONNECTION:
        raise PermissionError("owner relay refused startup admission")
    doc = answer.control()
    if (set(doc) != {"op", "principal", "uid"} or doc["op"] != "OWNER_IS"
            or not isinstance(doc["principal"], str) or not doc["principal"].strip()
            or type(doc["uid"]) is not int or doc["uid"] < 0):
        raise PermissionError("owner relay refused startup admission")
    root = Path(data).resolve()
    root.mkdir(parents=True, exist_ok=True)
    from tinyassets.boxes.boxd import OwnerBoxd

    tools = OwnerBoxd(owner_id=doc["principal"], owner_root=root,
                     state_dir=root / ".owner-boxd")
    os.environ["TINYASSETS_DATA_DIR"] = str(root)
    os.environ["TINYASSETS_ENGINE_MCP_TOOLS"] = "1"
    os.environ["TINYASSETS_ONBOARDING_APP"] = "1"
    _runtime = OwnerRuntime(doc["principal"], doc["uid"], root, os.getpid(), tools,
                            Path(relay).resolve())
    install_outbound_guard()
    return _runtime


def broker_egress():
    """Transport acquisition, never a host supervisor or a credential lease."""
    from tinyassets.owner_channel import OwnerChannel

    runtime = current()
    if runtime is None or runtime.relay is None:
        raise RuntimeError("owner broker channel is unavailable")
    return OwnerChannel(runtime.relay)


_guard_installed = False


def install_outbound_guard():
    """One socket audit seam for SDKs, HTTP libraries and raw Python sockets.

    The box's null netns is the boundary for native code and child processes.
    Loopback is the local engine transport; AF_UNIX is the mounted box channel.
    """
    global _guard_installed
    if not _guard_installed:
        sys.addaudithook(_outbound_audit)
        _guard_installed = True


def _outbound_audit(event, args):
    if event not in {"socket.connect", "socket.sendto", "socket.sendmsg",
                     "socket.getaddrinfo", "socket.gethostbyname"} or _runtime is None:
        return
    import ipaddress

    if event in {"socket.getaddrinfo", "socket.gethostbyname"}:
        host = args[0]
    else:
        sock = args[0]
        if sock.family == socket.AF_UNIX:
            return
        address = args[-1]
        if not isinstance(address, tuple) or not address:
            raise PermissionError("owner mode requires broker egress")
        host = address[0]
    if host in {"localhost", b"localhost"}:
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise PermissionError("owner mode requires broker egress")


@contextmanager
def serving():
    """Start the existing in-process engine endpoints and owner tick with the app."""
    from tinyassets import engine_mcp_http
    from tinyassets.control_plane.tick import AssignedQueueConsumer

    runtime = current()
    if runtime is None:
        yield
        return
    stop, stopped = threading.Event(), threading.Event()
    maintenance, watcher = _start_maintenance(stop)
    engine_mcp_http.start_engine_mcp_http_servers(
        runtime.root, stop_event=stop, stopped_event=stopped,
    )
    consumer = AssignedQueueConsumer(runtime.root)
    try:
        consumer.start()
        yield
    finally:
        consumer.stop()
        stop.set()
        if watcher is not None:
            watcher.set()
        if maintenance is not None:
            maintenance.join(30)
        wake = engine_mcp_http._SUPERVISOR_WAKE_EVENTS.get(runtime.root)
        if wake is not None:
            wake.set()
            if not stopped.wait(30):
                raise RuntimeError("owner engine endpoints did not stop")


def _start_maintenance(stop: threading.Event):
    """Owner recovery and maintenance formerly started by the shared entrypoint."""
    import logging

    logger = logging.getLogger(__name__)
    maintenance = None
    # Served-budget maintenance for ALL transports (Codex re-review 2026-08-19:
    # boot reconcile + the lease reconciler were streamable-http-only, so sse/
    # stdio startup skipped the promised orphan cleanup). Boot reconciliation
    # settles reservations orphaned by a crashed/killed prior process (at boot
    # nothing is in-flight, so any open row is dead and safe to release); the
    # periodic reconciler then settles per-call-lease-expired holds mid-run so a
    # crashed turn's hold never bricks serving until the next reboot. Cheap +
    # idempotent + a no-op when there are no reservations.
    try:
        import threading as _threading

        from tinyassets.provider_assignment import (
            reconcile_orphaned_reservations_on_boot,
            reconcile_served_budget_leases,
        )
        from tinyassets.storage import data_dir as _sb_data_dir

        _reclaimed = reconcile_orphaned_reservations_on_boot(_sb_data_dir())
        # Delivery intent survives queued-run startup interruption. Reconcile
        # ordinary runs first, then let the fenced receiver worker distinguish
        # proven unstarted attempts from possibly executed work.
        from tinyassets.api.runs import _ensure_runs_recovery
        from tinyassets.delivery_runtime import reconcile_deliveries
        from tinyassets.run_file_retention import reconcile_run_files
        from tinyassets.run_input_origins import reconcile_admitted_runs

        _admitted_run_cursor = ""
        _file_retention_cursor = ""
        try:
            _file_retention_cursor = reconcile_run_files(_sb_data_dir())
        except Exception:  # noqa: BLE001 - file debt must not disable other maintenance
            logger.exception("run files: boot retention failed")
        try:
            _ensure_runs_recovery()
            reconcile_deliveries(_sb_data_dir())
        except Exception:  # noqa: BLE001 - delivery must not disable budget recovery
            logger.exception("delivery: boot reconciliation failed")
        try:
            from tinyassets.consumer_runtime import initialize as initialize_consumer

            initialize_consumer(_sb_data_dir())
            _admitted_run_cursor = reconcile_admitted_runs(_sb_data_dir())
        except Exception:  # noqa: BLE001 - held admissions must not disable maintenance
            logger.exception("admitted runs: boot reconciliation failed")
        if _reclaimed:
            logger.info(
                "served budget: released %d orphaned reservation(s) at boot",
                _reclaimed,
            )

        def _served_budget_lease_loop() -> None:

            nonlocal _admitted_run_cursor, _file_retention_cursor
            while not stop.wait(300.0):
                try:
                    from tinyassets.command_center_update_maintenance import tick as update_centers

                    update_centers(_sb_data_dir())
                except Exception:  # noqa: BLE001 - keep unrelated maintenance running
                    logger.exception("command center updates: maintenance unavailable")
                try:
                    _file_retention_cursor = reconcile_run_files(
                        _sb_data_dir(), after_operation_id=_file_retention_cursor,
                    )
                except Exception:  # noqa: BLE001 - retain debt and continue other maintenance
                    logger.exception("run files: retention tick failed")
                try:
                    _admitted_run_cursor = reconcile_admitted_runs(
                        _sb_data_dir(), after_run_id=_admitted_run_cursor,
                    )
                except Exception:  # noqa: BLE001 - independent bounded origin nomination
                    logger.exception("admitted runs: reconciliation tick failed")
                try:
                    reconcile_deliveries(_sb_data_dir())
                except Exception:  # noqa: BLE001 - do not starve budget settlement
                    logger.exception("delivery: reconciliation tick failed")
                try:
                    _n = reconcile_served_budget_leases(_sb_data_dir())
                    if _n:
                        logger.info(
                            "served budget: lease-reconciled %d stale "
                            "reservation(s)",
                            _n,
                        )
                except Exception:  # noqa: BLE001 - loop must never die
                    logger.exception(
                        "served budget: lease reconciliation tick failed"
                    )

        maintenance = _threading.Thread(
            target=_served_budget_lease_loop,
            name="served-budget-lease-reconciler",
            daemon=True,
        )
        maintenance.start()
        from tinyassets.command_center_update_maintenance import scheduled as updates_scheduled

        updates_scheduled(_sb_data_dir())
    except Exception:  # noqa: BLE001 - boot must not fail on budget maintenance
        logger.exception("served budget: maintenance not started")
        try:
            from tinyassets.command_center_update_maintenance import (
                unavailable as updates_unavailable,
            )
            from tinyassets.storage import data_dir as update_data_dir

            updates_unavailable(update_data_dir())
        except Exception:  # noqa: BLE001 - the original startup failure stays authoritative
            logger.exception("command center updates: availability could not be recorded")

    # Take the run-recovery lock and interrupt what the previous process left in
    # flight BEFORE starting anything that runs: the engine MCP children serve
    # run tools too, and whichever process sweeps must be the one whose runs
    # are not in the table yet. A deploy-killed run is announced as interrupted
    # here, which is what lets an owner's run_completed loop survive a deploy.
    # The maintenance block above also calls it, but inside a try that an
    # earlier failure skips; this call is the one boot can rely on (once-only).
    from tinyassets.api.runs import _ensure_runs_recovery, start_run_owner_watcher

    # This process is the execution owner. Its tree exists, and is advertised in
    # the environment, BEFORE anything is spawned: engine children and workers
    # started below inherit it and join it, so the owner's death proof covers
    # them (change execution-owner-lease D2). Every transport, not only HTTP.
    from tinyassets.owner_lease import ensure_owner_tree
    from tinyassets.storage import data_dir as _owner_data_dir

    ensure_owner_tree(_owner_data_dir())
    _ensure_runs_recovery()
    # And keep recovering: an engine child that dies mid-run while this server
    # lives is found within one tick, by proof that it died.
    return maintenance, start_run_owner_watcher()
