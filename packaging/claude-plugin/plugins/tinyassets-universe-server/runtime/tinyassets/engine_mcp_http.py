"""Persistent, supervised, per-universe HTTP engine MCP servers.

The founder-scoped engine MCP tools (``read_graph`` / ``get_status`` /
``run_graph``) reach the sandboxed ``claude -p`` universe-intelligence turn
through a local MCP server. The claude CLI's **stdio** MCP spawn is unreliable
in the headless served subprocess (verified live 2026-08-19: the stdio server
never launched and the CLI reported the server "still connecting"). The **HTTP**
transport connects reliably, so the engine server runs over HTTP.

This starts one loopback HTTP engine server per SERVING universe and KEEPS them
running — so the capability survives a container recreate AND a lone engine-server
crash, with no host tending it (the founder's "24/7 without this computer" rule).
Each server is PINNED to exactly one ``(founder actor, universe graph)`` in its own handler module,
binds ``127.0.0.1`` only, and requires a per-server bearer secret on every request
(Codex gate #6 — the loopback listener is reachable by any in-container process).

Each server is control plane, not an owner engine: it holds the canonical
handlers over shared stores, so it never enters an owner cell (per-role D9/F1).
The untrusted side is the provider CLI, which runs as its owner in a
provider-exec cell and reaches only its own center's server, through a pinned
relay socket forwarded to the route's loopback port inside the cell, plus the
bearer. Handlers run in the daemon, sharing its bootstrap-installed broker and mapper
clients. No additional daemon-UID interpreter is spawned.

Admission follows current serving ownership and admin ACL, not a vetted-universe
list. Deleted or ambiguous owners fail closed. A versioned owner/port/secret route map is
published (mode 0600) and consumed by the shared ``read_engine_mcp_route`` reader.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: First loopback port; each serving universe gets the next free one.
ENGINE_MCP_HTTP_BASE_PORT = 8790
#: Private route map used by all engine-tool transport consumers.
ROUTES_FILENAME = ".engine_mcp_http_routes.json"
#: How often the supervisor respawns dead servers + reconciles serving intent.
_SUPERVISOR_INTERVAL_S = 15.0
_SUPERVISOR_WAKE_EVENTS: dict[Path, threading.Event] = {}
_STARTUP_WAIT_MAX_S = 30.0
_STARTUP_POLL_S = 0.1


def notify_engine_serving_changed(*, actor_id: str, graph_id: str, root: Path) -> None:
    """Warm a newly admitted universe using only this process's existing supervisor."""
    event = _SUPERVISOR_WAKE_EVENTS.get(root.resolve())
    if event is not None and engine_tools_authorized(
        actor_id=actor_id, graph_id=graph_id, root=root,
    ) and read_engine_mcp_route(actor_id=actor_id, graph_id=graph_id, root=root) is None:
        event.set()


async def _probe_loopback(route: EngineMcpRoute, *, timeout: float) -> bool:
    """Check only TCP readiness, sending no bytes and never an MCP handshake."""
    from urllib.parse import urlsplit

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        await asyncio.wait_for(
            asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", urlsplit(route.url).port)),
            timeout=min(timeout, 0.25),
        )
        return True
    except (OSError, TimeoutError):
        return False
    finally:
        sock.close()


async def wait_for_engine_mcp_route(
    *, actor_id: str, graph_id: str, root: Path, timeout: float,
) -> EngineMcpRoute | None:
    """Bound startup lag before the first protocol byte; never retry an MCP call.

    No local supervisor means no wait: other processes keep the immediate route
    contract. The reader and every per-tool authority recheck remain nonblocking.
    """
    event = _SUPERVISOR_WAKE_EVENTS.get(root.resolve())
    if event is None:
        return read_engine_mcp_route(actor_id=actor_id, graph_id=graph_id, root=root)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + min(timeout, _STARTUP_WAIT_MAX_S)
    woke = False
    while engine_tools_authorized(actor_id=actor_id, graph_id=graph_id, root=root):
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        route = read_engine_mcp_route(actor_id=actor_id, graph_id=graph_id, root=root)
        if route is not None and await _probe_loopback(route, timeout=remaining):
            # Authority or the route can change while the socket connect yields.
            if read_engine_mcp_route(actor_id=actor_id, graph_id=graph_id, root=root) == route:
                return route
        if not engine_tools_authorized(actor_id=actor_id, graph_id=graph_id, root=root):
            return None
        if not woke:
            event.set()
            woke = True
        remaining = deadline - loop.time()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(_STARTUP_POLL_S, remaining))
    return None


@dataclass(frozen=True, slots=True)
class EngineMcpRoute:
    """Private transport pin, not a grant or a server-liveness receipt."""

    actor_id: str
    graph_id: str
    url: str
    secret: str = field(repr=False)
    #: Signs each launch's tool grant (``served_tools.launch_grant``). Held by the
    #: daemon and the engine server only; a provider launch receives the bearer
    #: and a signed grant, never this key.
    grant_key: str = field(default="", repr=False)


def _unique_route_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("ambiguous engine route record")
        result[key] = value
    return result


def read_engine_mcp_route(
    *, actor_id: str, graph_id: str, root: Path | None = None,
) -> EngineMcpRoute | None:
    """Read the exact caller-owned route, never infer an actor from the graph.

    Callers supply their already verified principal and universe. Re-read each
    time; a prior route cannot stand in for changed configuration or permissions.
    This checks current owner admission and transport consistency; tools still
    enforce their operation-specific authority.
    """
    if not all(
        isinstance(value, str) and value and value == value.strip() and value.isprintable()
        for value in (actor_id, graph_id)
    ):
        return None
    from tinyassets.storage import data_dir

    try:
        base = data_dir() if root is None else Path(root)
        if not base.is_absolute():
            return None
        if not engine_tools_authorized(actor_id=actor_id, graph_id=graph_id, root=base):
            return None
        routes = json.loads(
            (base / ROUTES_FILENAME).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_route_keys,
        )
        entry = routes.get(graph_id) if isinstance(routes, dict) else None
        if not isinstance(entry, dict):
            return None
        if type(entry.get("version")) is not int or entry["version"] != 1:
            return None
        if entry.get("actor_id") != actor_id:
            return None
        port, secret = entry.get("port"), entry.get("secret")
        if type(port) is not int or not 1 <= port <= 65535:
            return None
        if not isinstance(secret, str) or re.fullmatch(r"[A-Za-z0-9_-]{32,}", secret) is None:
            return None
        grant_key = entry.get("grant_key", "")
        if not isinstance(grant_key, str) or (
            grant_key and re.fullmatch(r"[A-Za-z0-9_-]{32,}", grant_key) is None
        ):
            return None
        return EngineMcpRoute(
            actor_id, graph_id, f"http://127.0.0.1:{port}/mcp", secret, grant_key,
        )
    except (OSError, ValueError, TypeError, RecursionError):
        # No raw record/exception logging: the private file contains bearers.
        return None


def _engine_mcp_enabled() -> bool:
    return os.environ.get("TINYASSETS_ENGINE_MCP_TOOLS", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def engine_tools_authorized(*, actor_id: str, graph_id: str, root: Path | None = None) -> bool:
    """Recheck a process/request pin against current, non-deleted serving ownership.

    Neither environment pins nor private route records grant authority. A stale
    server must refuse even before the supervisor has reconciled a revocation.
    """
    from tinyassets.storage import data_dir

    if not _engine_mcp_enabled() or not all(
        isinstance(value, str) and value and value == value.strip() and value.isprintable()
        for value in (actor_id, graph_id)
    ):
        return False
    base = data_dir() if root is None else Path(root)
    if not base.is_absolute():
        return False
    return (graph_id, actor_id) in _serving_universe_owners(base, graph_id=graph_id)


def _serving_universe_owners(base: Path, *, graph_id: str | None = None) -> list[tuple[str, str]]:
    """Current unambiguous serving creators with admin ACL; read-only, fail closed.

    Do not join away competing/malformed creators: ambiguity denies the entire
    universe. Do not impose founder-home ownership on other administered universes.
    """
    import sqlite3

    from tinyassets.principals import has_named_principal
    from tinyassets.storage import db_path
    from tinyassets.storage.current_home import CurrentHomeChanged, check_principal_not_deleted

    try:
        conn = sqlite3.connect(db_path(base).as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("BEGIN")
            rows = conn.execute(
                "SELECT DISTINCT universe_id, created_by "
                "FROM agent_bindings WHERE status = 'serving'"
                + (" AND universe_id = ?" if graph_id is not None else ""),
                (graph_id,) if graph_id is not None else (),
            ).fetchall()
            candidates: dict[str, set] = {}
            for uid, owner in rows:
                candidates.setdefault(uid, set()).add(owner)
            owners: list[tuple[str, str]] = []
            for uid, creators in candidates.items():
                if len(creators) != 1:
                    continue
                owner = next(iter(creators))
                if not all(
                    isinstance(value, str) and value and value == value.strip()
                    and value.isprintable()
                    for value in (uid, owner)
                ) or not has_named_principal(owner):
                    continue
                if conn.execute(
                    "SELECT 1 FROM universe_acl WHERE universe_id = ? "
                    "AND actor_id = ? AND permission = 'admin'", (uid, owner),
                ).fetchone() is None:
                    continue
                try:
                    check_principal_not_deleted(conn, owner)
                except CurrentHomeChanged:
                    continue
                owners.append((uid, owner))
            return owners
        finally:
            conn.close()
    except (sqlite3.Error, OSError, ValueError, RuntimeError):
        # Never log raw DB errors or authority records from this credential rail.
        logger.warning("engine http: current serving authority unavailable")
        return []


class _EngineServer:
    """One pinned loopback endpoint using the daemon's installed role clients."""

    def __init__(self, universe_id, owner, port, data_dir_env):
        self.universe_id, self.owner, self.port = universe_id, owner, port
        self.secret, self.grant_key = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self._data_dir = data_dir_env
        self.thread = self.server = self.endpoint = None

    def start(self) -> bool:
        import uvicorn

        from tinyassets.engine_endpoint import EngineEndpoint
        from tinyassets.storage import data_dir

        if Path(self._data_dir).resolve() != data_dir().resolve():
            raise ValueError("engine endpoint root differs from the daemon root")
        if self.alive():
            raise RuntimeError("engine endpoint is already serving")
        if self.endpoint is not None:
            self.endpoint.close()
        self.endpoint = EngineEndpoint(self.owner, self.universe_id, self.secret, self.grant_key)
        self.server = uvicorn.Server(uvicorn.Config(
            self.endpoint, host="127.0.0.1", port=self.port, log_level="warning",
        ))
        self.thread = threading.Thread(
            target=self.server.run, name=f"engine-mcp-{self.universe_id}", daemon=True,
        )
        self.thread.start()
        return True

    def alive(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                raise RuntimeError("engine endpoint did not retire; its port remains reserved")
        if self.endpoint is not None:
            self.endpoint.close()


def _write_routes(root: Path, servers) -> None:
    routes = {
        s.universe_id: {
            "version": 1,
            "actor_id": s.owner,
            "port": s.port,
            "url": f"http://127.0.0.1:{s.port}/mcp",
            "secret": s.secret,
            # No key publishes no key: that server's launches then hold no ``ta``.
            "grant_key": getattr(s, "grant_key", ""),
        }
        for s in servers
    }
    path = root / ROUTES_FILENAME
    # Atomic publish: write a private temp then rename, so a concurrent turn
    # never reads a half-written map or a URL without its secret (Codex
    # 2026-08-19 — race-free publication).
    tmp = path.with_suffix(".json.tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(routes).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(str(tmp), str(path))
        os.chmod(path, 0o600)  # secrets — never world-readable
    except OSError:
        logger.exception("engine http: could not write route map")


def _desired_owners(root: Path) -> dict[str, str]:
    if not _engine_mcp_enabled():
        return {}
    owners: dict[str, set[str]] = {}
    for universe, owner in _serving_universe_owners(root):
        owners.setdefault(universe, set()).add(owner)
    return {universe: next(iter(found)) for universe, found in owners.items() if len(found) == 1}


def start_engine_mcp_http_servers(base: str | Path | None = None) -> list:
    """Start one auth'd loopback engine server per admitted serving universe
    and a daemon supervisor that respawns crashes + reconciles serving intent.

    No-op returning ``[]`` when the engine-MCP flag is off. Called once, early
    in daemon startup; the supervisor also handles later serving admission.
    """
    if not _engine_mcp_enabled():
        return []

    from tinyassets.broker.supervisor import _protect_daemon
    from tinyassets.storage import data_dir

    root = Path(data_dir() if base is None else base)
    if not root.is_absolute():
        raise ValueError("engine MCP data root must be absolute")
    data_dir_env = str(root)
    # Serving begins only after the daemon has retired its bootstrap authority.
    _protect_daemon()

    servers: dict[str, _EngineServer] = {}
    used_ports: set[int] = set()
    wake = threading.Event()

    def _next_port() -> int:
        port = ENGINE_MCP_HTTP_BASE_PORT
        while port in used_ports:
            port += 1
        used_ports.add(port)
        return port

    def _retire(uid: str) -> None:
        srv = servers.get(uid)
        if srv is not None:
            srv.stop()
            servers.pop(uid)
            used_ports.discard(srv.port)  # release the port (Codex 2026-08-19 d3)

    # Initial servers for whatever is serving now.
    for universe_id, owner in _desired_owners(root).items():
        srv = _EngineServer(universe_id, owner, _next_port(), data_dir_env)
        if srv.start():
            servers[universe_id] = srv
    _write_routes(root, servers.values())

    def _supervise() -> None:
        while True:
            wake.wait(_SUPERVISOR_INTERVAL_S)
            wake.clear()
            try:
                current = _desired_owners(root)
                changed = False
                # Retire universes whose current serving authority was removed.
                for uid in [u for u in servers if u not in current]:
                    _retire(uid)
                    changed = True
                # Retire+replace a universe whose OWNER changed (a stale founder
                # pin would answer as the wrong identity — Codex 2026-08-19 d2).
                for uid in [u for u in servers if servers[u].owner != current.get(u)]:
                    _retire(uid)
                    changed = True
                # Respawn crashed servers for still-desired universes.
                for uid, srv in servers.items():
                    if not srv.alive():
                        logger.warning("engine http: respawning dead server %s", uid)
                        srv.start()
                        changed = True
                # Stand up servers for newly-serving (or re-owned) universes.
                for uid, owner in current.items():
                    if uid not in servers:
                        srv = _EngineServer(uid, owner, _next_port(), data_dir_env)
                        if srv.start():
                            servers[uid] = srv
                            changed = True
                if changed:
                    _write_routes(root, servers.values())
            except Exception:  # noqa: BLE001 - the supervisor must never die
                logger.exception("engine http: supervisor tick failed")

    # ALWAYS start the supervisor — even when nothing is serving yet at boot — so
    # a universe that begins serving later gets a server (Codex 2026-08-19 d1).
    threading.Thread(
        target=_supervise, name="engine-mcp-supervisor", daemon=True
    ).start()
    _SUPERVISOR_WAKE_EVENTS[root.resolve()] = wake
    return list(servers.values())
