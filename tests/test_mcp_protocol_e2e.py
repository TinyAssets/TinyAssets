"""Real broker IPC, scanner and pinned HTTP against a local July MCP server."""
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.test_broker_server import broker  # noqa: F401 - real broker fixture
from tests.test_broker_upstream_stream import _driver
from tinyassets.broker.aclient import AsyncBrokerClient
from tinyassets.broker.ops import new_op_id
from tinyassets.mcp_remote import Binding, RemoteEndpoint, RemoteMcp, SignInRequired
from tinyassets.storage.outbound_connections import BrokerStream, ConnectionSecretBundle

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
    reason="owner=connect-anything-ladder; runs-in=Linux oracle; real SO_PEERCRED broker",
)
PREFIX = "io.modelcontextprotocol/"


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["json", "sse", "challenge"])
async def test_local_standard_server_through_real_broker(broker, encoding):  # noqa: F811
    seen, effects, violations = [], [], []
    consent = threading.Event()
    challenge = 'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource"'

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_POST(self):  # noqa: N802
            doc = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(doc)
            params, method = doc["params"], doc["method"]
            meta = params.get("_meta", {})
            valid = (meta.get(PREFIX + "protocolVersion") == "2026-07-28"
                     and self.headers.get("MCP-Protocol-Version") == "2026-07-28"
                     and self.headers.get("Mcp-Method") == method
                     and "MCP-Session-Id" not in self.headers
                     and self.headers.get("Authorization") == "Bearer synthetic-test-secret")
            if method == "tools/call":
                valid &= self.headers.get("Mcp-Name") == params.get("name")
            if not valid:
                violations.append(doc)
            status = 200 if valid else 400
            if encoding == "challenge":
                status, result = 401, {}
            elif method == "server/discover":
                result = {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}},
                          "_meta": {PREFIX + "serverInfo": {"name": "Local", "version": "1"}}}
            elif method == "tools/list":
                result = {"tools": [{"name": "write", "inputSchema": {"type": "object"}}]}
            elif method != "tools/call":
                status, result = 404, {}
            elif not consent.is_set():
                result = {"resultType": "input_required", "requestState": "opaque/+state=",
                          "inputRequests": {"login": {"method": "elicitation/create",
                          "params": {"mode": "url", "url": "https://mcp.example/login",
                                     "message": "Authorize the pending write"}}}}
            elif (params.get("requestState") == "opaque/+state=" and
                  params.get("inputResponses") == {"login": {"action": "accept"}}):
                effects.append(params["arguments"])
                result = {"content": [{"type": "text", "text": "completed"}]}
            else:
                status, result = 400, {}
                violations.append(doc)
            result.setdefault("resultType", "complete")
            document = {"jsonrpc": "2.0", "id": doc["id"], "result": result}
            if status == 400:
                document = {"jsonrpc": "2.0", "id": doc["id"],
                            "error": {"code": -32020, "message": "header mismatch"}}
            elif status == 404:
                document = {"jsonrpc": "2.0", "id": doc["id"],
                            "error": {"code": -32601, "message": "method not found"}}
            payload = json.dumps(document).encode()
            if encoding == "sse":
                payload = b": comment\r\n\r\ndata: " + payload + b"\r\n\r\n"
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream" if encoding == "sse"
                             else "application/json")
            if status == 401:
                self.send_header("WWW-Authenticate", challenge)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            self.wfile.flush()

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    driver, port = _driver(http)

    def upstream():
        request = broker.sent[-1][3]
        stream = driver.open_stream(
            bundle=ConnectionSecretBundle(token="synthetic-test-secret"), auth_scheme="bearer",
            method="POST", url=request["url"], headers=request["headers"], body=request["body"])
        return BrokerStream(stream, ("synthetic-test-secret",))

    broker.upstreams["next"] = upstream
    client = AsyncBrokerClient(broker.path, principal="alice", command_center="cc-alice",
                               fence=lambda: (broker.state["generation"], broker.state["token"]))
    binding = Binding("grant-a", "conn-a", "instance", RemoteEndpoint(
        f"https://mcp.example:{port}/mcp"))
    prompts = []

    async def host(context):
        assert context.binding == binding
        prompts.append(context)
        consent.set()
        return "accept"

    remote = RemoteMcp(client, binding, check_authority=lambda _: None, elicit_url=host)
    try:
        if encoding == "challenge":
            with pytest.raises(SignInRequired) as caught:
                await remote.discover()
            assert caught.value.www_authenticate == challenge
            assert len(seen) == 1
        else:
            await remote.discover()
            result = await remote.call("write", {"value": "one"},
                                       catalog_hash=remote.catalog_hash, op_id=new_op_id())
            assert result == {"resultType": "complete",
                              "content": [{"type": "text", "text": "completed"}]}
            assert effects == [{"value": "one"}]
            assert len(prompts) == 1
            assert len(seen) == 3
            assert len({doc["id"] for doc in seen}) == 3
        assert violations == []
    finally:
        await client.close()
        http.shutdown()
        http.server_close()
        thread.join(2)
