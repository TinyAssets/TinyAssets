"""One connector, two uses, no patch (openspec change ``unify-connection-uses``).

Founder, 2026-09-24: an LLM is just another universe connection, and the
connection-request notification is the setup. These tests connect two
providers the platform has never heard of through the SAME mechanism, with no
code for either:

* **quillmind** - an LLM speaking the standard chat wire at a path of its own.
  One ``connect`` answer creates the connection, grant and model use, makes it
  the unpowered universe's model, and it answers a model turn WITH TOOLS.
* **tasklark** - a platform API that wants a constant version header on every
  call. One ``connect`` answer creates it, and a workflow effect reaches it
  with the header applied by the broker, not retyped by the node.

Both run the real request rail, real vault, real ledger, real serving bind,
real selection and the real credential-blind broker dispatch, in process, to a
loopback server standing in for the two vendors (the effector suite's seam:
the spawned child cannot reach a loopback). Neither vendor name appears in
``tinyassets/`` - the last test pins that.
"""

from __future__ import annotations

import json

import pytest

from tests.inference_usage_helpers import synthetic_stream
from tests.support.broker_double import seed_owner_admission
from tests.test_authenticated_external_call_effector import (  # noqa: F401
    _install_loopback_driver,
    _Loopback,
    _the_owners_rules_allow,  # autouse: the owner's policy allows this call
)
from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import DevAuthProvider, Identity

OWNER = "owner-1"
UID = "u-owner"
LLM_KEY = "qm-live-" + "k" * 30
PLATFORM_KEY = "tl-live-" + "p" * 30
_VENDORS = ("quillmind", "tasklark")


class _Auth:
    def __init__(self, identity):
        self.identity = identity

    def resolve_token(self, token):
        return self.identity if token == "valid" else None

    def is_auth_required(self):
        return True

    def register_client(self, metadata):
        return {"client_id": "test-client", **metadata}

    def create_authorization(self, *a, **k):
        return "test-code"

    def exchange_code(self, *a, **k):
        return None


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """A founder's own unpowered universe, logged in as its owner."""
    from tests.support.shared_catalog import install
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    base = tmp_path / "data"
    install(monkeypatch, base)
    (base / UID).mkdir(parents=True)
    seed_owner_admission(base, principal=OWNER, center=UID)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    # Served turns use engine tools (on in production; dark by default here).
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    grant_universe_access(base, universe_id=UID, actor_id=OWNER, permission="admin",
                          granted_by=OWNER)
    set_founder_home(base, founder_sub=OWNER, universe_id=UID, platform_generated=True)
    set_provider(_Auth(Identity(user_id=OWNER, username=OWNER,
                                capabilities=["tinyassets.universe.write"])))
    auth_middleware("valid")
    yield base
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _ask(action, fields):
    from tinyassets.api.pending_requests import request_from_user

    return request_from_user(universe_id=UID, payload=json.dumps({
        "kind": "API", "title": "Connect it", "body": "", "action": action,
        "fields": fields,
    }))


def _answer(request_id, values):
    from tests.owner_answer import answer_request

    return answer_request(universe_id=UID, payload=json.dumps(
        {"request_id": request_id, "values": values}))


def _broker_proxy(base, grant_id, destination, runtime):
    """The exact production broker dispatch, in process (no child spawn)."""
    from tinyassets.credential_vault import custody_namespace
    from tinyassets.storage.outbound_connections import _build_credential_broker_dispatch

    dispatch = _build_credential_broker_dispatch({
        "allow_test_fixtures": False,
        "allow_http_connections": True,
        "ledger_db_path": str((base / ".broker" / "outbound.db").resolve()),
        "data_root": str(base.resolve()),
        "universe_dir": str(custody_namespace(base, OWNER)),
        "provider": "http",
        "destination": destination,
        "connection_type": "http",
        "owner_user_id": OWNER,
        "runtime_root": str(runtime.resolve()),
    })

    class _Proxy:
        closed = False

        def stream(self, *args, **kwargs):
            return synthetic_stream(self, *args, **kwargs)

        def request(self, verb, request, *, inference_usage=None,
                    policy_approval=None, policy_grant_ref=None, policy_agent=None,
                    policy_task_id=None):
            return dispatch(
                grant_id, verb, request,
                **({"policy_approval": policy_approval} if policy_approval else {}),
                **({"policy_grant_ref": policy_grant_ref,
                    "policy_agent": policy_agent,
                    "policy_task_id": policy_task_id} if policy_grant_ref else {}),
                **({"inference_usage": inference_usage.document(),
                    "operation_id": inference_usage.operation_id}
                   if inference_usage is not None else {}),
            )

        def close(self):
            self.closed = True

    return _Proxy()


QUILLMIND_ASK = {
    "type": "connect",
    "destination": "quillmind",
    "auth_scheme": "bearer",
    "host": "api.quillmind.dev",
    "path_template": "/v2/converse",
    "methods": ["POST"],
    "uses": {"model": {
        "wire": "chat_messages",
        "models": [{"id": "quill-large", "tools": True, "context": 65536}],
        "billing": "free",
    }},
}
TASKLARK_ASK = {
    "type": "connect",
    "destination": "tasklark",
    "auth_scheme": "bearer",
    "host": "api.tasklark.io",
    "path_template": "/v1/tasks",
    "methods": ["POST"],
    "uses": {"call": {}},
    "constant_headers": {"X-Api-Version": "2026-07"},
}
_KEY_FIELD = [{"name": "secret", "type": "secret", "label": "API key"}]


# --------------------------------------------------------------------------- #
# Dialects are data, resolved by structural name; stored names still resolve.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# The declarations are validated where they are stored.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# (a) A never-seen LLM: one answer, then a model turn with tools.
# --------------------------------------------------------------------------- #


def _tool_call_reply(method, path, body):
    request = json.loads(body)
    assert request["tools"], "the model turn must carry the engine tools"
    return json.dumps({
        "model": "quill-large-2026",
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "read_graph", "arguments": '{"target": "status"}'},
            }]},
        }],
        "usage": {"prompt_tokens": 21, "completion_tokens": 7},
    }).encode()


# --------------------------------------------------------------------------- #
# (b) A never-seen platform: one answer, then an authenticated call whose
#     constant header the broker applies.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# The agent edits the same fields later; everyone else sees not_found.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Money floor: a declared list never stands in for a priced catalogue.
# --------------------------------------------------------------------------- #

PRICED_ASK = {
    "type": "connect_http",
    "destination": "priced",
    "auth_scheme": "bearer",
    "endpoints": [
        {"host": "owned.example", "path_template": "/api/v1/models/user", "methods": ["GET"],
         "allowed_query": ["output_modalities"], "required_query": ["output_modalities"],
         "query_patterns": {"output_modalities": "^all$"}},
        {"host": "owned.example", "path_template": "/api/v1/benchmarks", "methods": ["GET"]},
        {"host": "owned.example", "path_template": "/api/v1/chat/completions",
         "methods": ["POST"]},
    ],
}
PAID_AS_FREE = {"model": {
    "wire": "chat_messages",
    "models": [{"id": "expensive/paid-model", "tools": True, "context": 200000}],
    "billing": "free",
}}


def _priced_connection(base):
    """A deposited connection with a priced model catalogue, as first power makes."""
    from tests.test_model_discovery_capability import DESCRIPTOR
    from tinyassets.api.http_connection import _ids
    from tinyassets.storage.outbound_connections import ConnectionLedger

    answered = _answer(_ask(PRICED_ASK, _KEY_FIELD)["request_id"], {"secret": LLM_KEY})
    assert answered["status"] == "answered", answered
    ledger = ConnectionLedger(base / ".broker" / "outbound.db", data_root=base)
    grant = ledger.get_grant(_ids(owner=OWNER, destination="priced")[1])
    ledger.configure_capability(
        connection_id=grant.connection_id, capability_kind="model_discovery",
        descriptor=DESCRIPTOR, enabled=True, expected_grant=grant,
    )
    return ledger, grant


@pytest.mark.verifies('owner-daemon-in-box:T2')
def test_agent_cannot_relabel_a_priced_connections_paid_model_as_free(owner):
    from tinyassets.api.connection_uses import configure_connection

    ledger, grant = _priced_connection(owner)
    # The universe's own agent, through configure.
    refused = configure_connection(universe_id=UID, payload=json.dumps({
        "destination": "priced", "uses": PAID_AS_FREE,
    }))
    assert refused.get("error") == "connection_setup_invalid", refused
    # Through a connect ask on the same connection: refused before it is shown.
    asked = _ask({**PRICED_ASK, "type": "connect", "uses": PAID_AS_FREE}, _KEY_FIELD)
    assert asked.get("error") == "connection_setup_invalid", asked
    assert "priced model catalogue" in asked["detail"]
    # And at the storage boundary, whoever calls it.
    with pytest.raises(ValueError, match="priced model catalogue"):
        ledger.configure_capability(
            connection_id=grant.connection_id, capability_kind="model_use",
            descriptor=PAID_AS_FREE["model"], enabled=True,
        )
    assert ledger.get_connection_capability(grant.connection_id, "model_use") is None


@pytest.mark.verifies('owner-daemon-in-box:T2')
def test_a_declaration_cannot_replace_accepted_spending_limits(owner, monkeypatch):
    from types import SimpleNamespace

    from tinyassets.api.connection_uses import apply_connection_uses
    from tinyassets.api.http_connection import _ids
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers.definition import register_definition

    answered = _answer(_ask({**PRICED_ASK, "destination": "capped"}, _KEY_FIELD)[
        "request_id"], {"secret": LLM_KEY})
    assert answered["status"] == "answered", answered
    grant_id = _ids(owner=OWNER, destination="capped")[1]
    definition = register_definition(
        universe_id=UID, owner_user_id=OWNER, access_method="api_key_http",
        protocol="chat_messages", model="seed", ref=grant_id,
    )
    paid = ModelAccess("explicit", ("expensive/paid-model",), (
        ("input_million_tokens_usd", 5_000_000), ("output_million_tokens_usd", 5_000_000),
        ("request_usd", 0)))
    monkeypatch.setattr(
        "tinyassets.provider_assignment.load_provider_assignment",
        lambda *_a, **_k: SimpleNamespace(candidates=(SimpleNamespace(
            provider=f"api_key_http:{definition.id}", access=paid),)),
    )
    refused = apply_connection_uses(
        base=owner, uid=UID, actor=OWNER, grant_id=grant_id,
        uses=PAID_AS_FREE, constant_headers={}, owner_confirmed=True,
    )
    assert refused["error"] == "connection_setup_invalid"
    assert "accepted spending" in refused["detail"]


# --------------------------------------------------------------------------- #
# A constant header can never shadow or duplicate the key header.
# --------------------------------------------------------------------------- #


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_a_differently_cased_header_cannot_shadow_or_duplicate_the_key(monkeypatch):
    import http.server
    import threading

    from tinyassets.storage import outbound_connections as oc

    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            return

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            seen.append(self.headers.get_all("X-Api-Key"))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _install_loopback_driver(monkeypatch, server.server_address[1])
        driver = oc._SsrfHardenedHttpDriver()
        driver(
            bundle=oc._build_http_secret_bundle("header", "real-vault-key"),
            auth_scheme="header", header_name="X-Api-Key", method="POST",
            url="https://api.example.com/v1/x", headers={"x-api-key": "shadow"},
            body={"a": 1},
            allowed_endpoints=oc._parse_allowed_endpoints([{
                "host": "api.example.com", "path_template": "/v1/x", "methods": ["POST"]}]),
        )
    finally:
        server.shutdown()
        server.server_close()
    assert seen == [["real-vault-key"]]


# --------------------------------------------------------------------------- #
# No vendor code: the platform never names either provider.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# The app agent reaches configure on the served surface, as the owner only.
# --------------------------------------------------------------------------- #


@pytest.fixture
def served(monkeypatch):
    from tinyassets import universe_tools as engine

    monkeypatch.setattr(engine, "_ACTOR_ID", "owner-setup")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-setup")
    monkeypatch.setattr(engine, "_engine_run_admit", lambda **kw: True)
    from tests.engine_authority_helpers import mock_engine_admission
    mock_engine_admission(monkeypatch, {"u-setup"})
    return engine


from tests.support.egress import admitted_egress  # noqa: E402, F401
