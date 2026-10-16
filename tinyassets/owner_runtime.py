"""Process startup state for a daemon inside its one owner's box."""

from __future__ import annotations

import os
import socket
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


_runtime: OwnerRuntime | None = None

#: boxhostd bind-mounts the owner's durable tree here (one-box-per-owner design;
#: ``boxes/gvisor_runtime``). It is the only data root an owner daemon serves.
OWNER_MOUNT = Path("/home/owner")


def _is_owner_mount(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink() and os.path.ismount(path)


def current() -> OwnerRuntime | None:
    if _runtime is not None and _runtime.pid != os.getpid():
        raise PermissionError("owner daemon admission requires a fresh process startup")
    return _runtime


def start(relay: Path) -> OwnerRuntime:
    """Read identity once from the per-box socket, before constructing the app.

    The data root is the owner mount boxhostd provides, never a caller-chosen
    path: anything else would put the owner's databases outside the durable,
    quota-bound tree and lose them on restart.
    """
    global _runtime
    if _runtime is not None:
        raise RuntimeError("owner runtime is already initialized")
    if not _is_owner_mount(OWNER_MOUNT):
        raise PermissionError(f"owner daemon requires the boxhostd owner mount at {OWNER_MOUNT}")
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
    root = OWNER_MOUNT
    from tinyassets.boxes.boxd import OwnerBoxd

    tools = OwnerBoxd(owner_id=doc["principal"], owner_root=root,
                     state_dir=root / ".owner-boxd")
    os.environ["TINYASSETS_DATA_DIR"] = str(root)
    os.environ["TINYASSETS_ENGINE_MCP_TOOLS"] = "1"
    os.environ["TINYASSETS_ONBOARDING_APP"] = "1"
    _runtime = OwnerRuntime(doc["principal"], doc["uid"], root, os.getpid(), tools)
    return _runtime


def broker_egress(*args, **kwargs):
    """Slice 3 installs model/connection egress over the admitted box channel."""
    raise NotImplementedError("owner daemon broker egress is not installed")


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
        wake = engine_mcp_http._SUPERVISOR_WAKE_EVENTS.get(runtime.root)
        if wake is not None:
            wake.set()
            if not stopped.wait(30):
                raise RuntimeError("owner engine endpoints did not stop")
