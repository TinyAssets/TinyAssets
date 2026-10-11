"""Synthetic upstreams for image acceptance; no application objects are replaced.

This server has no network uplink. Its TLS CA and signing key are disposable.
The production verifier checks its JWTs, and the production broker sends real
HTTPS to its service. Only the model's choices and upstream responses are fixed.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import io
import json
import secrets
import shlex
import ssl
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

ADDRESS = "93.184.216.34"
HOSTS = (
    "tinyassets.io",
    "auth.capability.test",
    "model.capability.test",
    "service.capability.test",
)
OWNER = "alice"
ISSUER = "https://auth.capability.test"
RESOURCE = "https://tinyassets.io/mcp"
MODEL_KEY = "sk-ant-oat01-synthetic-capability-only"


def prepare(directory: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Capability fixture")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host) for host in HOSTS]), False)
        .sign(key, hashes.SHA256())
    )
    directory.joinpath("ca.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    directory.joinpath("key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    directory.joinpath("hosts").write_text(
        "127.0.0.1 localhost\n" + ADDRESS + " " + " ".join(HOSTS) + "\n"
    )


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)
    elif isinstance(value, str):
        # `ta` returns JSON text followed by the shell's exit status.
        try:
            decoded, _ = json.JSONDecoder().raw_decode(value.lstrip())
        except ValueError:
            return
        if isinstance(decoded, (dict, list)):
            yield from objects(decoded)


class Fixture:
    def __init__(self, directory):
        import jwt
        from cryptography.hazmat.primitives import serialization

        self.key = serialization.load_pem_private_key((directory / "key.pem").read_bytes(), None)
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwk.update(kid="synthetic", use="sig", alg="RS256")
        self.codes = {}
        self.calls = []
        self.scenario = None
        self.errors = []

    def token(self):
        import jwt

        return jwt.encode(
            dict(
                sub=OWNER,
                iss=ISSUER,
                aud=RESOURCE,
                iat=int(time.time()),
                exp=int(time.time()) + 3600,
                email="capability@example.invalid",
                scope="openid profile email offline_access",
            ),
            self.key,
            algorithm="RS256",
            headers={"kid": "synthetic"},
        )

    def scenario_steps(self, nonce, connection):
        from PIL import Image

        image = io.BytesIO()
        Image.new("RGB", (3, 2), (23, 89, 177)).save(image, format="PNG")
        png = base64.b64encode(image.getvalue()).decode()
        path = "/u/capability-" + nonce + ".txt"
        request = json.dumps({"request": {"path": "/probe", "body": {"nonce": nonce}}})
        return [
            ("write", "write", dict(path=path, content="before-" + nonce)),
            ("read", "read", dict(path=path)),
            (
                "edit",
                "edit",
                dict(path=path, old_text="before-" + nonce, new_text="after-" + nonce),
            ),
            ("edit", "read", dict(path=path)),
            (
                "bash",
                "bash",
                dict(
                    command="cat "
                    + shlex.quote(path)
                    + " && printf %s "
                    + shlex.quote(png)
                    + " | base64 -d > /u/capability-image.png"
                    + " && curl -fsS https://service.capability.test/health"
                    + " && (for i in $(seq 1 100); do /bin/true & done; wait)"
                ),
            ),
            ("read_image", "read", dict(path="/u/capability-image.png")),
            (
                "connected_service",
                "bash",
                dict(
                    command="ta connection:" + connection + ":POST --json " + shlex.quote(request)
                ),
            ),
            (
                "patch_request",
                "bash",
                dict(
                    command="ta write_graph --json "
                    + shlex.quote(
                        json.dumps(
                            dict(
                                target="patch_request",
                                operation="send",
                                payload_json=json.dumps(
                                    dict(
                                        title="Synthetic capability " + nonce,
                                        details="CI fixture only " + nonce,
                                    )
                                ),
                            )
                        )
                    )
                ),
            ),
        ]


def serve(directory: Path):
    fixture = Fixture(directory)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def reply(self, status, value, *, headers=None):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if self.headers.get("Host") == "tinyassets.io":
                return self.proxy()
            if parsed.path == "/oauth2/jwks":
                return self.reply(200, {"keys": [fixture.jwk]})
            if parsed.path == "/oauth2/authorize":
                query = parse_qs(parsed.query)
                assert query["resource"] == [RESOURCE]
                assert query["code_challenge_method"] == ["S256"]
                code = secrets.token_urlsafe(24)
                fixture.codes[code] = (query["code_challenge"][0], query["redirect_uri"][0])
                return self.reply(
                    302,
                    {},
                    headers={
                        "Location": query["redirect_uri"][0]
                        + "?"
                        + urlencode(dict(code=code, state=query["state"][0]))
                    },
                )
            if parsed.path == "/evidence":
                return self.reply(
                    200, dict(scenario=fixture.scenario, calls=fixture.calls, errors=fixture.errors)
                )
            if parsed.path == "/health":
                return self.reply(200, {"capability_tls": "verified"})
            return self.reply(200, {})

        def do_POST(self):
            try:
                if self.headers.get("Host") == "tinyassets.io":
                    return self.proxy()
                size = int(self.headers.get("Content-Length", "0"))
                assert 0 <= size <= 16 * 1024 * 1024
                raw = self.rfile.read(size)
                if self.path == "/oauth2/token":
                    data = parse_qs(raw.decode())
                    if data["grant_type"] == ["refresh_token"]:
                        assert data["refresh_token"] == ["synthetic-refresh"]
                    else:
                        challenge, redirect = fixture.codes.pop(data["code"][0])
                        actual = (
                            base64.urlsafe_b64encode(
                                hashlib.sha256(data["code_verifier"][0].encode()).digest()
                            )
                            .decode()
                            .rstrip("=")
                        )
                        assert challenge == actual and redirect == data["redirect_uri"][0]
                    return self.reply(
                        200,
                        dict(
                            access_token=fixture.token(),
                            token_type="Bearer",
                            expires_in=3600,
                            refresh_token="synthetic-refresh",
                        ),
                    )
                data = json.loads(raw or b"{}")
                if self.path == "/scenario":
                    assert len(data["nonce"]) == 24 and data["nonce"].isalnum()
                    fixture.scenario = dict(
                        nonce=data["nonce"],
                        index=0,
                        verified=[],
                        steps=fixture.scenario_steps(data["nonce"], data["connection"]),
                    )
                    return self.reply(200, {})
                if self.path == "/probe":
                    assert self.headers.get("Authorization") == "Bearer capability-service-token"
                    assert isinstance(data["nonce"], str)
                    fixture.calls.append(data["nonce"])
                    return self.reply(200, {"observed": data["nonce"]})
                if self.path.split("?")[0].endswith("/v1/messages"):
                    return self.model(data)
                return self.reply(200, {})
            except Exception as exc:
                fixture.errors.append(type(exc).__name__ + ": " + str(exc))
                if self.path.split("?")[0].endswith("/v1/messages"):
                    return self.model(data, failure="CAPABILITY-FAILED")
                self.reply(500, {"error": fixture.errors[-1]})

        def proxy(self):
            connection = http.client.HTTPConnection("127.0.0.1", 8001, timeout=180)
            size = int(self.headers.get("Content-Length", "0"))
            headers = {
                k: v
                for k, v in self.headers.items()
                if k.lower() not in ("connection", "transfer-encoding")
            }
            connection.request(self.command, self.path, self.rfile.read(size), headers)
            response = connection.getresponse()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in ("connection", "transfer-encoding"):
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            finally:
                connection.close()
                self.close_connection = True

        def model(self, body, failure=None):
            from role_chat_probe import advertised_tools

            state = fixture.scenario
            offered = advertised_tools(body)
            conversation = not failure and any(name.split("__")[-1] == "write" for name in offered)
            tool = None
            if conversation:
                assert state is not None, "model requested before scenario was armed"
                index = state["index"]
                if index:
                    call_id = "capability_" + state["nonce"] + "_" + str(index - 1)
                    returned = [
                        item
                        for item in objects(body)
                        if item.get("type") == "tool_result" and item.get("tool_use_id") == call_id
                    ]
                    assert returned, "missing tool result"
                    encoded = json.dumps(returned)
                    assert not any(item.get("is_error") for item in returned), encoded[:1500]
                    assert "error:" not in encoded.lower(), encoded[:500]
                    capability = state["steps"][index - 1][0]
                    if capability == "read":
                        assert "before-" + state["nonce"] in encoded
                        state["verified"].append("write")
                    if capability == "edit" and state["steps"][index - 1][1] == "read":
                        assert "after-" + state["nonce"] in encoded, encoded[:1500]
                    if capability == "bash":
                        assert "after-" + state["nonce"] in encoded
                        assert "capability_tls" in encoded
                    if capability == "read_image":
                        from PIL import Image

                        images = [
                            item["source"]["data"]
                            for item in objects(returned)
                            if item.get("type") == "image" and isinstance(item.get("source"), dict)
                        ]
                        assert images, "image block absent at model boundary"
                        pixels = Image.open(io.BytesIO(base64.b64decode(images[0]))).convert("RGB")
                        assert pixels.size == (3, 2) and pixels.getpixel((1, 1)) == (23, 89, 177)
                    if capability == "connected_service":
                        assert state["nonce"] in fixture.calls and state["nonce"] in encoded, (
                            encoded[:1500]
                        )
                        state["verified"].append("ta")
                    if capability == "patch_request":
                        assert "delivery_id" in encoded and "sent" in encoded, encoded[:1500]
                        state["delivery_id"] = next(
                            item["delivery_id"]
                            for item in objects(returned)
                            if item.get("delivery_id")
                        )
                    if capability != "write" and not (
                        capability == "edit" and state["steps"][index - 1][1] == "edit"
                    ):
                        state["verified"].append(capability)
                if index < len(state["steps"]):
                    _, name, arguments = state["steps"][index]
                    (selected,) = [item for item in offered if item.split("__")[-1] == name]
                    tool = dict(
                        type="tool_use",
                        id="capability_" + state["nonce"] + "_" + str(index),
                        name=selected,
                        input=arguments,
                    )
                    state["index"] += 1
            text = "CAPABILITY-REPLY-" + state["nonce"] if conversation and not tool else "Working."
            if failure:
                text = failure
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()

            def event(kind, **data):
                payload = {"type": kind, **data}
                self.wfile.write(
                    ("event: " + kind + "\ndata: " + json.dumps(payload) + "\n\n").encode()
                )
                self.wfile.flush()

            event(
                "message_start",
                message=dict(
                    id="msg_capability",
                    type="message",
                    role="assistant",
                    model=body["model"],
                    content=[],
                    stop_reason=None,
                    stop_sequence=None,
                    usage=dict(input_tokens=12, output_tokens=0),
                ),
            )
            event("content_block_start", index=0, content_block=dict(type="text", text=""))
            for part in (text[:10], text[10:]):
                event("content_block_delta", index=0, delta=dict(type="text_delta", text=part))
                if conversation and not tool:
                    state["reply_deltas"] = state.get("reply_deltas", 0) + 1
                time.sleep(0.15)
            event("content_block_stop", index=0)
            if tool:
                event("content_block_start", index=1, content_block={**tool, "input": {}})
                event(
                    "content_block_delta",
                    index=1,
                    delta=dict(type="input_json_delta", partial_json=json.dumps(tool["input"])),
                )
                event("content_block_stop", index=1)
            event(
                "message_delta",
                delta=dict(stop_reason="tool_use" if tool else "end_turn", stop_sequence=None),
                usage=dict(output_tokens=20),
            )
            event("message_stop")
            self.close_connection = True

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / "ca.pem", directory / "key.pem")
    server = ThreadingHTTPServer(("0.0.0.0", 443), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return fixture, server
