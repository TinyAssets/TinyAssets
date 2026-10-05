"""Remote MCP crosses the real broker socket and its attachment send fence."""
# ruff: noqa: F811
import json
import socket
import sys
from dataclasses import asdict

import pytest

from tests.test_broker_ledger_queries import ledger  # noqa: F401
from tests.test_broker_server import Script, broker  # noqa: F401
from tests.test_mcp_attachment import draft, operation
from tests.test_mcp_remote import TOOLS
from tinyassets.broker.aclient import AsyncBrokerClient
from tinyassets.mcp_attachment import Attachment
from tinyassets.mcp_remote import Binding, RemoteMcp
from tinyassets.storage.outbound_connections import ConnectionLedger, GrantResolutionError

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
    reason="Unix peer credentials; runs-in=Linux oracle",
)


@pytest.mark.asyncio
async def test_real_broker_initialization_pagination_and_revocation(broker, ledger):
    broker.server._ledger_for = lambda principal: ConnectionLedger(
        ledger._db_path, verify_authenticated_principal=lambda: principal)
    operation(ledger, value=draft())
    configured = draft(revision=2, state="connecting")
    operation(ledger, value=configured, expected=draft())
    binding = Binding("grant-alice", "conn-alice", ledger.incarnation("conn-alice"),
                      Attachment.parse(configured))

    def response():
        doc = json.loads(broker.sent[-1][3]["body"])
        method = doc["method"]
        if method == "notifications/initialized":
            return Script([], status=202)
        result = ({"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
                  if method == "initialize" else
                  {"tools": TOOLS[:1], "nextCursor": "next"}
                  if method == "tools/list" and not doc["params"] else
                  {"tools": TOOLS[1:]} if method == "tools/list" else
                  {"content": [{"type": "text", "text": "broker result"}]})
        payload = json.dumps({"jsonrpc": "2.0", "id": doc["id"], "result": result}).encode()
        script = Script([b"data: " + payload[:15], payload[15:] + b"\n\n"])
        script.headers = {"Content-Type": "text/event-stream"}
        if method == "initialize":
            script.headers["MCP-Session-Id"] = "bound-session"
        return script

    broker.upstreams["next"] = response
    client = AsyncBrokerClient(broker.path, principal="alice", command_center="cc-alice",
                                fence=lambda: (broker.state["generation"], broker.state["token"]))
    remote = RemoteMcp(client, binding, check_authority=lambda _: None)
    try:
        assert await remote.discover() == TOOLS
        from tinyassets.broker.ops import new_op_id

        count = len(broker.sent)
        with pytest.raises(GrantResolutionError, match="authority"):
            await remote.call("read", {}, catalog_hash=remote.catalog_hash, op_id=new_op_id())
        assert len(broker.sent) == count
        # The activation coordinator is not implemented in this slice. Seed its
        # future committed result solely to test the active transport boundary.
        active = configured | {"revision": 3, "state": "active"}
        with ledger._connect() as conn:
            conn.execute("UPDATE mcp_attachments SET descriptor_json=? WHERE connection_id=?",
                         (json.dumps(active), "conn-alice"))
        binding = Binding(binding.grant_id, binding.connection_id, binding.incarnation,
                          Attachment.parse(active))
        remote = RemoteMcp(client, binding, check_authority=lambda _: None)
        assert await remote.discover() == TOOLS
        operation_id = new_op_id()
        assert await remote.call("read", {}, catalog_hash=remote.catalog_hash,
                                 op_id=operation_id) == {
            "content": [{"type": "text", "text": "broker result"}]}
        outcome = await remote.reconcile(operation_id)
        assert outcome["state"] == "completed"
        assert outcome["side_effect_state"] == "unknown"
        count = len(broker.sent)
        from tinyassets.broker.client import BrokerRefused

        with pytest.raises(BrokerRefused):
            await remote.call("read", {}, catalog_hash=remote.catalog_hash, op_id=operation_id)
        assert len(broker.sent) == count
        foreign_client = AsyncBrokerClient(
            broker.path, principal="bob", command_center="cc-alice",
            fence=lambda: (broker.state["generation"], broker.state["token"]))
        count = len(broker.sent)
        try:
            foreign = RemoteMcp(foreign_client, binding, check_authority=lambda _: None)
            with pytest.raises(GrantResolutionError):
                await foreign.discover()
            assert len(broker.sent) == count
        finally:
            await foreign_client.close()
        operation(ledger, value=asdict(Attachment.parse(active)) | {
            "revision": 4, "state": "revoked"}, expected=active)
        count = len(broker.sent)
        with pytest.raises(GrantResolutionError):
            await remote.call("read", {}, catalog_hash=remote.catalog_hash, op_id=new_op_id())
        assert len(broker.sent) == count
        assert ledger._get_connection_resource("conn-alice").revoked_at is None
    finally:
        await client.close()
