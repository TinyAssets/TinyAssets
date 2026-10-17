"""Current-owner startup wait ends before any MCP request or model inference."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests import test_engine_tool_client as tool_tests
from tests import test_workflow_http_agent as workflow_tests
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import engine_mcp_http as routes
from tinyassets import engine_tool_client as client
from tinyassets.storage import DB_FILENAME

fake = tool_tests.fake
route = tool_tests.route
http_wire = workflow_tests.http_wire
work_agent = workflow_tests.work_agent
run_workflow = workflow_tests.run


pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def supervisor(monkeypatch, route):
    state = SimpleNamespace(wakes=0, probes=0)

    def wake():
        state.wakes += 1

    monkeypatch.setattr(routes, "_SUPERVISOR_WAKE_EVENTS",
                        {route[0].resolve(): SimpleNamespace(set=wake)}, raising=False)

    async def probe(*args, **kwargs):
        state.probes += 1
        return True

    monkeypatch.setattr(routes, "_probe_loopback", probe, raising=False)
    return state


async def _open(timeout=0.1):
    async with client.open_engine_tools(actor_id="actor-a", graph_id="u-a",
                                        enabled_tools=["read_graph"], timeout=timeout):
        pass


@pytest.fixture
def startup_clock(monkeypatch):
    """Control only the route waiter's clock; real DB admission takes real time.

    The 20ms contract must not require filesystem reads to finish in 20ms.
    Replacing this module's asyncio binding leaves the actual event loop and
    client transport untouched. The fake probe spends the logical budget.
    """
    state = SimpleNamespace(now=0.0)

    def advance(seconds):
        state.now += seconds

    async def sleep(seconds):
        advance(seconds)

    monkeypatch.setattr(routes, "asyncio", SimpleNamespace(
        get_running_loop=lambda: SimpleNamespace(time=lambda: state.now),
        sleep=sleep,
    ))
    return advance


@pytest.mark.asyncio
async def test_route_appears_after_two_polls_and_connects_once(
    fake, route, supervisor, monkeypatch,
):
    root, server = route
    (root / routes.ROUTES_FILENAME).unlink()
    original = routes.read_engine_mcp_route
    polls = []

    def read(**kwargs):
        polls.append(kwargs)
        if len(polls) == 3:
            routes._write_routes(root, [server])
        return original(**kwargs)

    monkeypatch.setattr(routes, "read_engine_mcp_route", read)
    monkeypatch.setattr(routes._EngineServer, "start",
                        lambda self: pytest.fail("waiter spawned a per-request server"))
    await _open(1)
    assert len(polls) >= 3
    assert supervisor.wakes == 1 and supervisor.probes == 1
    assert fake.lists == [None] and fake.calls == []


@pytest.mark.asyncio
async def test_unauthorized_never_wakes_or_probes(fake, route, supervisor):
    with sqlite3.connect(route[0] / DB_FILENAME) as conn:
        conn.execute("DELETE FROM universe_acl")
    with pytest.raises(client.EngineToolError, match="unavailable"):
        await _open()
    assert supervisor.wakes == supervisor.probes == 0
    assert fake.lists == fake.calls == []


@pytest.mark.asyncio
async def test_revocation_while_starting_stops_before_second_probe(
    fake, route, supervisor, monkeypatch,
):
    async def revoke(*args, **kwargs):
        supervisor.probes += 1
        with sqlite3.connect(route[0] / DB_FILENAME) as conn:
            conn.execute("DELETE FROM universe_acl")
        return False

    monkeypatch.setattr(routes, "_probe_loopback", revoke)
    with pytest.raises(client.EngineToolError, match="unavailable"):
        await _open()
    assert supervisor.probes == 1 and fake.lists == []


@pytest.mark.asyncio
async def test_never_listening_times_out_without_connect(
    fake, supervisor, monkeypatch, startup_clock,
):
    async def unavailable(*args, timeout):
        supervisor.probes += 1
        startup_clock(timeout)
        return False

    monkeypatch.setattr(routes, "_probe_loopback", unavailable)
    with pytest.raises(client.EngineToolError, match="unavailable"):
        await _open(0.02)
    assert supervisor.probes == 1 and fake.lists == fake.calls == []


@pytest.mark.asyncio
async def test_cancel_startup_wait_never_connects(fake, supervisor, monkeypatch):
    entered = asyncio.Event()

    async def pending(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(routes, "_probe_loopback", pending)
    task = asyncio.create_task(_open())
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
    assert fake.lists == fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"actor_id": "foreign"}, {"version": 7}, {"secret": "bad"}])
async def test_invalid_route_never_probes_or_connects(
    fake, route, supervisor, change, startup_clock,
):
    path = route[0] / routes.ROUTES_FILENAME
    document = json.loads(path.read_text())
    document["u-a"].update(change)
    path.write_text(json.dumps(document))
    with pytest.raises(client.EngineToolError, match="unavailable"):
        await _open(0.02)
    assert supervisor.wakes == 1 and supervisor.probes == 0 and fake.lists == []


@pytest.mark.asyncio
async def test_once_tcp_ready_mcp_failure_is_not_retried(fake, supervisor, monkeypatch):
    connections = []

    async def broken(self):
        connections.append(self)
        raise OSError("synthetic handshake failure")

    monkeypatch.setattr(type(fake), "__aenter__", broken)
    with pytest.raises(client.EngineToolError, match="unavailable"):
        await _open()
    assert supervisor.probes == len(connections) == 1
    assert fake.lists == []


@pytest.mark.asyncio
async def test_no_matching_supervisor_reads_once_without_wait(fake, route, monkeypatch):
    root = route[0]
    (root / routes.ROUTES_FILENAME).unlink()
    monkeypatch.setattr(routes, "_SUPERVISOR_WAKE_EVENTS", {}, raising=False)
    reads = []
    original = routes.read_engine_mcp_route

    def read(**kwargs):
        reads.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(routes, "read_engine_mcp_route", read)
    with pytest.raises(client.EngineToolError, match="unavailable"):
        await _open()
    assert len(reads) == 1 and fake.lists == []


@pytest.mark.asyncio
async def test_probe_accepts_only_tcp_and_sends_no_bytes(route):
    received = asyncio.get_running_loop().create_future()

    async def accepted(reader, writer):
        received.set_result(await reader.read())
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(accepted, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        pin = routes.EngineMcpRoute("actor-a", "u-a", f"http://127.0.0.1:{port}/mcp", "s" * 43)
        assert await routes._probe_loopback(pin, timeout=0.2)
        assert await asyncio.wait_for(received, 0.5) == b""
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_wait_respects_platform_cap(fake, supervisor, monkeypatch, startup_clock):
    observed = []

    async def closed(*args, timeout):
        observed.append(timeout)
        startup_clock(timeout)
        return False

    monkeypatch.setattr(routes, "_STARTUP_WAIT_MAX_S", 0.02)
    monkeypatch.setattr(routes, "_probe_loopback", closed)
    with pytest.raises(client.EngineToolError, match="unavailable"):
        await _open(60)
    assert len(observed) == 1 and 0 < observed[0] <= 0.02
    assert fake.lists == []


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_workflow_startup_failure_settles_without_inference(
    tmp_path, monkeypatch, authenticate_request, work_agent, mode,
):
    monkeypatch.setattr(routes, "_SUPERVISOR_WAKE_EVENTS",
                        {tmp_path.resolve(): SimpleNamespace(set=lambda: None)})
    monkeypatch.setattr(routes, "_STARTUP_WAIT_MAX_S", 0.02)

    async def unavailable(*args, **kwargs):
        if mode == "cancel":
            raise asyncio.CancelledError()
        return False

    monkeypatch.setattr(routes, "_probe_loopback", unavailable)
    if mode == "cancel":
        with pytest.raises(asyncio.CancelledError):
            run_workflow(tmp_path, monkeypatch, authenticate_request)
    else:
        result = run_workflow(tmp_path, monkeypatch, authenticate_request)
        assert result["terminal_status"] != "completed"
    assert work_agent.wires == work_agent.tools == []
    with sqlite3.connect(tmp_path / DB_FILENAME) as conn:
        rows = [json.loads(row[0]) for row in conn.execute(
            "SELECT record_json FROM provider_invocation_reservations",
        )]
    assert len(rows) == 1 and rows[0]["state"] == "cancelled_before_launch"


def test_serving_warmup_runs_after_binding_commit(tmp_path, monkeypatch):
    from tests.test_background_budget_finalization_e2e import _seed_serving_assignment

    calls = []

    def notify(**kwargs):
        with sqlite3.connect(tmp_path / DB_FILENAME) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT created_by FROM agent_bindings WHERE universe_id = ? AND status='serving'",
                (kwargs["graph_id"],),
            ).fetchall()
        calls.append((kwargs, rows))

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "notify_engine_serving_changed", notify)
    _seed_serving_assignment(tmp_path)
    assert len(calls) == 1
    assert calls[0][1] == [("acct_alice",)]
    assert calls[0][0] == {"actor_id": "acct_alice", "graph_id": "universe_alice", "root": tmp_path}


def test_warmup_only_signals_matching_authorized_unrouted_owner(route, supervisor):
    root = route[0]
    routes.notify_engine_serving_changed(actor_id="actor-a", graph_id="u-a", root=root)
    assert supervisor.wakes == 0  # Published routes need no approval warm-up.
    (root / routes.ROUTES_FILENAME).unlink()
    routes.notify_engine_serving_changed(actor_id="foreign", graph_id="u-a", root=root)
    assert supervisor.wakes == 0
    routes.notify_engine_serving_changed(actor_id="actor-a", graph_id="u-a", root=root / "other")
    assert supervisor.wakes == 0
    routes.notify_engine_serving_changed(actor_id="actor-a", graph_id="u-a", root=root)
    assert supervisor.wakes == 1
