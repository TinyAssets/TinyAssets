"""Invocation-scoped git rewrites using the existing broker smart-HTTP path."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

ENV = "TINYASSETS_BOX_GIT"


def environment(connections, agent):
    """Project already selected grants; credentials and fence tokens stay out."""
    from tinyassets.storage.workspace_authority import connection_git_host

    rows = [{"grant_id": grant.grant_id, "connection_id": view.connection_id,
             "incarnation": incarnation, "git_host": connection_git_host(view),
             "scopes": list(view.scopes)} for grant, view, incarnation in connections]
    body = json.dumps({"agent": agent, "connections": rows})
    if len(body.encode()) > 16384 or len(rows) > 32:
        raise ValueError("git launch scope exceeds bound")
    return {ENV: body}


def broker_client(path, **kwargs):
    from tinyassets.broker.client import BrokerClient

    return BrokerClient(path, **kwargs)


def prepare():
    """Called inside the immutable guest forwarder, before its command fork."""
    raw = os.environ.pop(ENV, None)
    if raw is None:
        return None, None
    # CLI-only forwarding runs with -S. Keep its no-git path stdlib-only.
    from tinyassets import rpc_frames as rf
    from tinyassets.boxes import git_routes

    if len(raw.encode()) > 16384:
        raise ValueError("git launch scope exceeds bound")
    document = json.loads(raw)
    if (set(document) != {"agent", "connections"}
            or not isinstance(document["agent"], str) or not document["agent"]
            or not isinstance(document["connections"], list)
            or len(document["connections"]) > 32):
        raise ValueError("invalid git launch scope")
    center, owner = os.environ["TINYASSETS_BOX_CENTER"], os.environ["TINYASSETS_BOX_OWNER"]

    def handshake(sock):
        sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CONNECT", "center": center}))
        answer = rf.read_frame_blocking(sock)
        if (answer is None or answer.stream != rf.CONNECTION or answer.kind != rf.CONTROL
                or answer.control() != {"op": "BOX_CONNECTED"}):
            raise PermissionError("owner git relay refused")

    client = broker_client(Path("/run/boxhostd/relay.sock"), principal=owner,
                          command_center=center, fence=lambda: (0, "owner-box"),
                          verify_peer=handshake)
    connections = []
    for row in document["connections"]:
        if (not isinstance(row, dict) or set(row) != {
            "grant_id", "connection_id", "incarnation", "git_host", "scopes"
        } or not isinstance(row["scopes"], list)
            or any(not isinstance(scope, str) for scope in row["scopes"])
            or any(not isinstance(row[key], str) for key in row if key != "scopes")):
            raise ValueError("invalid git connection projection")
        grant = SimpleNamespace(grant_id=row["grant_id"])
        view = SimpleNamespace(connection_id=row["connection_id"], git_host=row["git_host"],
                               scopes=tuple(row["scopes"]), connection_type="http",
                               allowed_endpoints=())
        connections.append((grant, view, row["incarnation"]))
    proxy = object()
    context = git_routes._open_routes(proxy, client, connections, document["agent"])
    rewrites = context.__enter__()
    os.environ.update(GIT_CONFIG_COUNT=str(len(rewrites)), GIT_TERMINAL_PROMPT="0")
    for i, (key, value) in enumerate(git_routes.config_pairs(rewrites)):
        os.environ[f"GIT_CONFIG_KEY_{i}"] = key
        os.environ[f"GIT_CONFIG_VALUE_{i}"] = value
    return context, proxy
