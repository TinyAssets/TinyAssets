"""Private daemon RPC for registered-client offers and vault-only refresh.

A random capability is bound to a root, owner and universe at engine launch.
The child never supplies an owner, path, token bundle, endpoint or secret name.
Refresh replies contain no credentials: the broker re-reads its existing vault.
This is not a public HTTP route and is not exposed to a jailed program.
"""
from __future__ import annotations

import http.client
import http.server
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from tinyassets.connection_oauth.transport import OAuthError

ENV = "TINYASSETS_CONNECTION_OAUTH_SERVICE"
_lock = threading.Lock()
_server: http.server.ThreadingHTTPServer | None = None
_bindings: dict[str, tuple[Path, str]] = {}
_configs: dict[tuple[Path, str], dict[str, Any]] = {}
_MAX_BODY = 16384


def inherited_config() -> dict[str, Any] | None:
    raw = os.environ.get(ENV)
    if not raw:
        return None
    try:
        config = json.loads(raw)
        if not isinstance(config, dict):
            raise ValueError
        return config
    except ValueError:
        raise OAuthError("platform_client_unavailable") from None


def _dispatch(binding: tuple[Path, str], doc: dict[str, Any]) -> dict[str, Any]:
    from tinyassets.connection_oauth import directory
    from tinyassets.connection_oauth.discovery import validate_request
    from tinyassets.connection_oauth.tokens import ConnectionTokens
    from tinyassets.credential_vault import http_deposit_refusal
    from tinyassets.daemon_server import list_universe_acl
    from tinyassets.principals import named_principal

    universe, owner = binding
    if not any(r.get("actor_id") == named_principal(owner) and r.get("permission") == "admin"
               for r in list_universe_acl(universe.parent, universe_id=universe.name)):
        raise OAuthError("platform_client_unavailable")
    if doc.get("op") == "resolve":
        hosts = doc.get("hosts")
        if (not isinstance(hosts, list) or len(hosts) > 8
                or any(not isinstance(h, str) or len(h) > 253 for h in hosts)):
            raise OAuthError("platform_client_unavailable")
        return {"offer": directory.resolve(validate_request(doc.get("requested")), hosts)}
    if doc.get("op") == "refresh":
        destination = doc.get("destination")
        rejected = doc.get("rejected", "")
        if (not isinstance(destination, str) or not 1 <= len(destination) <= 128
                or not isinstance(rejected, str) or len(rejected) > 8192
                or http_deposit_refusal(universe, destination=destination, owner_user_id=owner)):
            raise OAuthError("platform_client_unavailable")
        tokens = ConnectionTokens(universe_dir=universe, owner_user_id=owner)
        tokens.current(destination, tokens._read(destination), rejected=rejected)
        return {"ok": True}
    raise OAuthError("platform_client_unavailable")


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        self.connection.settimeout(20)
        status, reply = 400, {"error": "platform_client_unavailable"}
        try:
            credential = self.headers.get("Authorization", "").removeprefix("Bearer ")
            with _lock:
                binding = _bindings.get(credential)
            size = int(self.headers.get("Content-Length", "0"))
            if binding is not None and self.path == "/" and 0 < size <= _MAX_BODY:
                doc = json.loads(self.rfile.read(size))
                if isinstance(doc, dict):
                    reply = _dispatch(binding, doc)
                    status = 200
        except Exception:  # noqa: BLE001 - no exception text crosses this boundary
            pass
        payload = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
        except OSError:
            pass


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # never print request handling exceptions


def client_config(universe: Path, owner: str) -> dict[str, Any]:
    """Called by daemon launchers; re-use the engine's binding inside children."""
    global _server
    inherited = inherited_config()
    if inherited:
        return inherited
    from tinyassets.connection_oauth.directory import prepare_children

    prepare_children()
    key = (Path(universe).resolve(), owner)
    with _lock:
        if key in _configs:
            return dict(_configs[key])
        if _server is None:
            _server = _Server(("127.0.0.1", 0), _Handler)
            threading.Thread(target=_server.serve_forever, daemon=True,
                             name="connection-oauth").start()
        token = secrets.token_urlsafe(32)
        _bindings[token] = key
        config = {"port": _server.server_port, "token": token}
        _configs[key] = config
        return dict(config)


def call(config: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """Fixed loopback destination, bounded I/O, and fixed errors only."""
    connection = None
    try:
        connection = http.client.HTTPConnection("127.0.0.1", int(config["port"]), timeout=60)
        connection.request("POST", "/", body=json.dumps(doc), headers={
            "Authorization": "Bearer " + config["token"], "Content-Type": "application/json",
        })
        response = connection.getresponse()
        raw = response.read(_MAX_BODY + 1)
        if response.status != 200 or len(raw) > _MAX_BODY:
            raise ValueError
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except Exception:  # noqa: BLE001 - no transport exception details
        raise OAuthError("platform_client_unavailable") from None
    finally:
        if connection is not None:
            connection.close()
