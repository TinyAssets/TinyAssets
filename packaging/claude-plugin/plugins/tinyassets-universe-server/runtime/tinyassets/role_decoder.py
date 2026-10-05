"""Daemon client for the launcher's fixed, data-free decoder cell."""
from __future__ import annotations

import array
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace


def decode(data: bytes, mime: str, universe_dir: Path):
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker import supervisor
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.storage import data_dir
    from tinyassets.tool_images import DECODE_WALL_SECONDS, MAX_IMAGE_BYTES, MAX_IMAGE_SOURCE_BYTES

    if not supervisor.broker_selected():
        raise RuntimeError("role decoder requires selected role launcher")
    supervisor._protect_daemon()
    if universe_dir is None:
        raise PermissionError("decoder requires admitted owner scope")
    universe = Path(universe_dir)
    root = data_dir().resolve()
    owner = current_identity().user_id
    if (universe.parent != root or universe.resolve() != universe
            or not (get_founder_home(root, owner) == universe.name or universe_access_permission(
                root, universe_id=universe.name, actor_id=owner) == "admin")
            or not isinstance(data, bytes) or len(data) > MAX_IMAGE_SOURCE_BYTES):
        raise PermissionError("decoder scope is not admitted")
    document = {"op": "SPAWN", "kind": "image-decoder", "principal": owner,
                "command_center": universe.name, "mime": mime}
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as control:
        control.settimeout(DECODE_WALL_SECONDS + 5)
        control.connect(str(supervisor.LAUNCHER_SOCKET))
        if supervisor._peer(control) != (os.getppid(), 0, 0):
            raise PermissionError("decoder launcher is not the daemon parent")
        parent, child = socket.socketpair()
        with parent, child:
            parent.settimeout(DECODE_WALL_SECONDS)
            control.sendmsg([json.dumps(document).encode()], [(
                socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [child.fileno()]))])
            child.close()
            parent.sendall(data)
            parent.shutdown(socket.SHUT_WR)
            output = bytearray()
            while part := parent.recv(65536):
                output.extend(part)
                if len(output) > MAX_IMAGE_BYTES + 16384:
                    raise RuntimeError("decoder output exceeds its bound")
        answer = json.loads(control.recv(4096))
    if answer.get("op") != "SPAWN_DONE" or type(answer.get("returncode")) is not int:
        raise RuntimeError("decoder launcher refused")
    header, _, payload = bytes(output).partition(b"\n")
    proof = json.loads(header) if header else {}
    cell = proof.get("cell", {})
    if (cell.get("uid") != 1003 or cell.get("gid") != 1003
            or cell.get("fds") != [0, 1, 2] or cell.get("caps") != "zero"
            or cell.get("nnp") != 1 or cell.get("profile") != "cell-deny"):
        raise RuntimeError("decoder cell proof is absent")
    return SimpleNamespace(returncode=answer["returncode"], stdout=payload, cell=cell)
