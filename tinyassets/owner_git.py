"""Invocation-scoped git routes served by the admitted owner daemon."""
from __future__ import annotations

import json
import socketserver
import threading
from contextlib import contextmanager
from types import SimpleNamespace

from tinyassets.boxes import git_routes
from tinyassets.boxes.network import _read_head

ENV = "TINYASSETS_BOX_GIT"


def environment(connections, agent):
    from tinyassets.storage.workspace_authority import connection_git_host

    rows = [{"grant_id": grant.grant_id, "connection_id": view.connection_id,
             "incarnation": incarnation, "git_host": connection_git_host(view),
             "scopes": list(view.scopes)} for grant, view, incarnation in connections]
    body = json.dumps({"agent": agent, "connections": rows})
    if len(body.encode()) > 16384 or len(rows) > 32:
        raise ValueError("git launch scope exceeds bound")
    return {ENV: body}


@contextmanager
def routes(client, connections, agent):
    proxy = object()
    slots = threading.BoundedSemaphore(32)

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            if not slots.acquire(blocking=False):
                return
            try:
                if not git_routes.serve(proxy, self.request, _read_head(self.request)):
                    self.request.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            finally:
                slots.release()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with git_routes._open_routes(proxy, client, connections, agent) as rewrites:
        with Server(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                pairs = git_routes.config_pairs(rewrites)
                yield {"GIT_CONFIG_COUNT": str(len(pairs)), "GIT_TERMINAL_PROMPT": "0",
                       "http_proxy": f"http://127.0.0.1:{server.server_address[1]}",
                       **{f"GIT_CONFIG_{kind}_{index}": value
                          for index, pair in enumerate(pairs)
                          for kind, value in zip(("KEY", "VALUE"), pair)}}
            finally:
                server.shutdown()
                thread.join(2)


@contextmanager
def for_command(env, home):
    extra = dict(env)
    raw = extra.pop(ENV, None)
    if raw is None:
        yield extra
        return
    from tinyassets.broker.client import BrokerClient
    from tinyassets.owner_runtime import broker_egress, require

    runtime = require(home)
    if len(raw.encode()) > 16384:
        raise ValueError("git launch scope exceeds bound")
    doc = json.loads(raw)
    if (set(doc) != {"agent", "connections"} or not isinstance(doc["agent"], str)
            or not doc["agent"] or not isinstance(doc["connections"], list)
            or len(doc["connections"]) > 32):
        raise ValueError("invalid git launch scope")
    records = []
    for row in doc["connections"]:
        if (not isinstance(row, dict) or set(row) != {
                "grant_id", "connection_id", "incarnation", "git_host", "scopes"}
                or not isinstance(row["scopes"], list)
                or any(not isinstance(scope, str) for scope in row["scopes"])
                or any(not isinstance(row[key], str) for key in row if key != "scopes") ):
            raise ValueError("invalid git connection projection")
        records.append((SimpleNamespace(grant_id=row["grant_id"]),
                        SimpleNamespace(connection_id=row["connection_id"],
                                        git_host=row["git_host"], scopes=tuple(row["scopes"]),
                                        connection_type="http", allowed_endpoints=()),
                        row["incarnation"]))
    channel = broker_egress()
    client = BrokerClient(channel.socket_path, principal=runtime.principal,
                          command_center=home.name, fence=channel.fence,
                          verify_peer=channel.verify_broker)
    with routes(client, records, doc["agent"]) as git_env:
        yield {**extra, **git_env}
