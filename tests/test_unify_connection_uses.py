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

import asyncio
import json
from pathlib import Path

import pytest

from tests.test_authenticated_external_call_effector import (  # noqa: F401
    _a_reviewer_that_approves,  # autouse: an explicit approving D1d reviewer
    _install_loopback_driver,
    _Loopback,
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
    from tinyassets.daemon_server import grant_universe_access, set_founder_home

    base = tmp_path / "data"
    (base / UID).mkdir(parents=True)
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
    from tinyassets.api.pending_requests import answer_request

    return answer_request(universe_id=UID, payload=json.dumps(
        {"request_id": request_id, "values": values}))


def _broker_proxy(base, grant_id, destination, runtime):
    """The exact production broker dispatch, in process (no child spawn)."""
    from tinyassets.storage.outbound_connections import _build_credential_broker_dispatch

    dispatch = _build_credential_broker_dispatch({
        "allow_test_fixtures": False,
        "allow_http_connections": True,
        "ledger_db_path": str((base / "outbound.db").resolve()),
        "universe_dir": str((base / UID).resolve()),
        "provider": "http",
        "destination": destination,
        "connection_type": "http",
        "owner_user_id": OWNER,
        "runtime_root": str(runtime.resolve()),
    })

    class _Proxy:
        closed = False

        def request(self, verb, request, *, inference_usage=None):
            return dispatch(
                grant_id, verb, request,
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


def test_bundled_dialects_resolve_by_structure_and_stored_alias():
    from tinyassets.providers import protocol_encoders as pe
    from tinyassets.providers.agent_wire_codec import agent_wire_for, installed_agent_wire
    from tinyassets.providers.wire_dialects import (
        UnknownDialect,
        canonical_dialect,
        dialect_names,
        same_dialect,
    )

    assert set(dialect_names()) == {"chat_messages", "content_blocks"}
    assert canonical_dialect("openai_chat") == "chat_messages"
    assert canonical_dialect("anthropic_messages") == "content_blocks"
    assert same_dialect("openai_chat", "chat_messages")
    assert not same_dialect("chat_messages", "content_blocks")
    with pytest.raises(UnknownDialect):
        canonical_dialect("a-wire-nobody-bundled")
    # A stored alias runs exactly the wire the structural name does.
    assert pe.PROTOCOLS["openai_chat"] is pe.PROTOCOLS["chat_messages"]
    assert pe.agent_codec_for("chat_messages") is not None
    assert pe.agent_codec_for("content_blocks") is None  # text-only dialect
    assert agent_wire_for("openai_chat") is agent_wire_for("chat_messages")
    assert installed_agent_wire() is agent_wire_for("chat_messages")
    # The wire's own header is document data, still sent for stored rows.
    assert pe.static_headers_for("anthropic_messages") == pe.static_headers_for(
        "content_blocks") != {}


def test_a_stored_definition_and_a_structural_one_both_register_and_run(owner):
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
    from tinyassets.providers.definition import register_definition

    for protocol in ("openai_chat", "chat_messages", "anthropic_messages", "content_blocks"):
        definition = register_definition(
            universe_id=UID, owner_user_id=OWNER, access_method="api_key_http",
            protocol=protocol, model="m", ref="http_grant_" + "a" * 32,
        )
        assert definition.protocol == protocol  # stored exactly; the id addresses it
        assert ApiKeyHttpProvider(definition).family == f"api:{protocol}"


# --------------------------------------------------------------------------- #
# The declarations are validated where they are stored.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("model,match", [
    ({"wire": "chat_messages", "models": [{"id": "m", "tools": True, "context": 1}],
      "billing": "metered"}, "metered billing needs prices"),
    ({"wire": "smoke_signals", "models": [{"id": "m", "tools": True, "context": 1}],
      "billing": "free"}, "bundled dialect"),
    ({"wire": "chat_messages", "models": [], "billing": "free"}, "models must list"),
    ({"wire": "chat_messages", "models": [{"id": "m", "tools": True}],
      "billing": "free"}, "context"),
])
def test_model_use_refuses_what_it_cannot_honour(model, match):
    from tinyassets.api.connection_uses import ConnectionUseError, validate_uses

    with pytest.raises(ConnectionUseError, match=match):
        validate_uses({"model": model})


@pytest.mark.parametrize("headers,match", [
    ({"Authorization": "Bearer x"}, "may not be set"),
    ({"X-Trace": "a" * 40}, "looks like a credential"),
    ({"X-Bad": "line\r\nInjected: 1"}, "single-line"),
    ({"Bad Name": "1"}, "header token"),
])
def test_constant_headers_are_never_credentials_or_framing(headers, match):
    from tinyassets.api.connection_uses import ConnectionUseError, validate_constant_headers

    with pytest.raises(ConnectionUseError, match=match):
        validate_constant_headers(headers)


def test_a_model_use_needs_somewhere_to_post(owner):
    out = _ask({**QUILLMIND_ASK, "methods": ["GET"]}, _KEY_FIELD)
    assert out["error"] == "request_invalid"
    assert "POST endpoint" in out["detail"]


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


def test_never_seen_llm_connects_from_the_request_and_answers_with_tools(
    owner, tmp_path, monkeypatch,
):
    from tinyassets.api.pending_requests import _serving_llm_bound
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.providers.agent_inference import AgentInferenceRequest
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.declared_models import DeclaredModelContract
    from tinyassets.providers.definition import get_definition
    from tinyassets.providers.model_selection import prepare_selected_model
    from tinyassets.providers.served_model_plan import _http_models

    base = owner
    assert _serving_llm_bound(base, UID, OWNER) is False

    asked = _ask(QUILLMIND_ASK, _KEY_FIELD)
    assert asked["status"] == "pending", asked
    assert "quill-large" in asked["grant_sentence"]
    assert "becomes its model" in asked["grant_sentence"]

    answered = _answer(asked["request_id"], {"secret": LLM_KEY})
    assert answered["status"] == "answered", answered
    assert answered["uses"] == ["model"]
    assert answered["serving"]["status"] == "serving", answered["serving"]
    provider = answered["provider"]
    assert provider == f"api_key_http:{answered['definition_id']}"
    assert answered["serving"]["provider"] == answered["definition_id"]
    assert LLM_KEY not in json.dumps(answered)
    # Deterministically selected: the universe is now powered by this connection.
    assert _serving_llm_bound(base, UID, OWNER) is True
    assignment = load_provider_assignment(base, universe_id=UID)
    member = next(m for m in assignment.candidates if m.provider == provider)
    assert member.access.model_scope == "explicit"
    assert member.access.model_ids == ("quill-large",)
    assert member.access.cost_caps is None  # free-only: nothing can be spent

    # The served plan admits the declared model as tool-capable, with no fetch.
    _snapshot, models, _interaction, _caps, rejected = _http_models(OWNER, UID, member)
    assert [m.model_id for m in models.models] == ["quill-large"]
    assert not rejected

    selection, recheck = prepare_selected_model(
        base_path=base, owner_user_id=OWNER, universe_id=UID, provider=provider,
        model_id="quill-large", access=member.access, needs_tools=True,
    )
    assert selection.supports_tools is True
    assert isinstance(selection.contract(), DeclaredModelContract)
    recheck()

    loop = _Loopback(responder=_tool_call_reply)
    _install_loopback_driver(monkeypatch, loop.port)
    definition = get_definition(UID, answered["definition_id"])
    proxy = _broker_proxy(base, answered["grant_id"], "quillmind", tmp_path / "rt-llm")
    tools = [{"type": "function", "function": {
        "name": "read_graph", "description": "Read the universe.",
        "parameters": {"type": "object", "properties": {"target": {"type": "string"}}},
    }}]
    from tinyassets.request_budget import TurnRequestBudget

    budget = TurnRequestBudget(OWNER, UID)
    budget.persist(base)
    ordinal = budget.reserve(owner=OWNER, universe=UID, source_ref=provider,
                             model=selection.model_id, free=False, purpose="reply")
    config = ModelConfig(
        engine_mcp_enabled=True, engine_mcp_actor_id=OWNER, engine_mcp_graph_id=UID,
        max_tokens=512, selected_model=selection,
        agent_request=AgentInferenceRequest(tools=tools),
        request_budget=budget, request_attempt=ordinal,
    )
    try:
        response = asyncio.run(ApiKeyHttpProvider(definition, proxy_override=proxy).complete(
            "What is my universe doing?", "You are the universe.", config,
            universe_dir=base / UID,
        ))
        budget.settle_invocation(ordinal, "succeeded")
    finally:
        budget.close()
        loop.stop()

    assert budget.receipt()["dispatched"] == 1
    assert [call.name for call in response.agent_reply.tool_requests] == ["read_graph"]
    assert response.reported_model == "quill-large-2026"
    assert (response.input_tokens, response.output_tokens) == (21, 7)
    (wire,) = loop.recorded
    assert wire["path"] == "/v2/converse"  # the vendor's own path, from the grant
    assert wire["headers"]["Authorization"] == f"Bearer {LLM_KEY}"
    sent = json.loads(wire["body"])
    assert sent["model"] == "quill-large"
    assert [t["function"]["name"] for t in sent["tools"]] == ["read_graph"]
    assert LLM_KEY not in json.dumps(sent)


def test_a_powered_universe_is_not_switched_by_a_new_model_connection(owner, monkeypatch):
    first = _answer(_ask(QUILLMIND_ASK, _KEY_FIELD)["request_id"], {"secret": LLM_KEY})
    assert first["serving"]["status"] == "serving"
    second_ask = {**QUILLMIND_ASK, "destination": "quillmind-backup",
                  "host": "backup.quillmind.dev"}
    second = _answer(_ask(second_ask, _KEY_FIELD)["request_id"], {"secret": LLM_KEY})
    assert second["status"] == "answered"
    assert second["serving"] == {"status": "unchanged", "reason": "already_powered"}


# --------------------------------------------------------------------------- #
# (b) A never-seen platform: one answer, then an authenticated call whose
#     constant header the broker applies.
# --------------------------------------------------------------------------- #


def test_never_seen_platform_connects_and_the_broker_applies_its_constant_header(
    owner, tmp_path, monkeypatch,
):
    from tinyassets.api.pending_requests import _serving_llm_bound
    from tinyassets.effectors import authenticated_external_call as aec
    from tinyassets.storage.effector_consents import grant_consent

    base = owner
    asked = _ask(TASKLARK_ASK, _KEY_FIELD)
    assert asked["status"] == "pending", asked
    assert "X-Api-Version: 2026-07" in asked["grant_sentence"]
    answered = _answer(asked["request_id"], {"secret": PLATFORM_KEY})
    assert answered["status"] == "answered", answered
    assert answered["uses"] == ["call"]
    assert answered["constant_headers"] == {"X-Api-Version": "2026-07"}
    assert "serving" not in answered  # a platform does not power the universe
    assert _serving_llm_bound(base, UID, OWNER) is False

    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    grant_consent(base / UID, sink=aec.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
                  destination="tasklark", granted_by=OWNER)
    loop = _Loopback(responder=lambda *_: b'{"id": "task-9"}')
    _install_loopback_driver(monkeypatch, loop.port)
    proxy = _broker_proxy(base, answered["grant_id"], "tasklark", tmp_path / "rt-call")
    monkeypatch.setattr(aec, "_open_connection_proxy", lambda **_k: proxy)
    packet = {
        "sink": aec.EXTERNAL_WRITE_SINK_AUTHENTICATED_CALL,
        "connection_id": answered["connection_id"],
        "grant_id": answered["grant_id"],
        "verb": "POST",
        # The node sends no version header: the connection declares it.
        "request": {"method": "POST", "path": "/v1/tasks",
                    "body": {"title": "Write the chapter"}},
    }
    try:
        evidence = aec.run_authenticated_external_call_effector(
            node_id="n1", output_keys=["out"], run_state={"out": json.dumps(packet)},
            base_path=str(base / UID), run_id="r1",
        )
    finally:
        loop.stop()

    assert evidence["delivered"] is True, evidence
    (wire,) = loop.recorded
    assert wire["path"] == "/v1/tasks"
    assert wire["headers"]["X-Api-Version"] == "2026-07"
    assert wire["headers"]["Authorization"] == f"Bearer {PLATFORM_KEY}"
    assert json.loads(wire["body"]) == {"title": "Write the chapter"}


def test_constant_header_wins_over_a_node_header_and_never_over_auth():
    from tinyassets.storage.outbound_connections import (
        ConstantHeadersCapability,
        merge_constant_headers,
    )

    capability = ConstantHeadersCapability("c", "constant_headers",
                                           (("X-Api-Version", "2"),))
    merged = merge_constant_headers(
        {"url": "u", "headers": {"x-api-version": "1", "Accept": "json"}}, capability)
    assert merged["headers"] == {"Accept": "json", "X-Api-Version": "2"}
    assert merge_constant_headers({"url": "u"}, None) == {"url": "u"}


# --------------------------------------------------------------------------- #
# The agent edits the same fields later; everyone else sees not_found.
# --------------------------------------------------------------------------- #


def test_configure_edits_a_held_connection_and_reads_back(owner):
    from tinyassets.api.cloud_connections import cloud_connections
    from tinyassets.api.connection_uses import configure_connection

    answered = _answer(_ask({**TASKLARK_ASK, "constant_headers": {}}, _KEY_FIELD)[
        "request_id"], {"secret": PLATFORM_KEY})
    assert answered["status"] == "answered", answered
    configured = configure_connection(universe_id=UID, payload=json.dumps({
        "destination": "tasklark", "constant_headers": {"X-Api-Version": "2026-08"},
        "uses": {"call": {}},
    }))
    assert configured["status"] == "configured", configured
    rows = cloud_connections(action="list", universe_id=UID)["connections"]
    (row,) = [r for r in rows if r["destination"] == "tasklark"]
    assert row["constant_headers"] == {"X-Api-Version": "2026-08"}
    assert row["uses"] == {"call": {}}

    missing = configure_connection(universe_id=UID, payload=json.dumps({
        "destination": "not-mine", "constant_headers": {"X-Api-Version": "1"},
    }))
    assert missing == {"error": "not_found", "resource": "connection"}


def test_configure_cannot_create_a_model_use_without_the_owner(owner):
    """A model list and its billing are a spend claim: the agent's own write
    (configure) may not make one; the owner's answer to a connect ask does."""
    from tinyassets.api.cloud_connections import cloud_connections
    from tinyassets.api.connection_uses import configure_connection

    plain = {k: v for k, v in QUILLMIND_ASK.items() if k != "uses"}
    assert _answer(_ask({**plain, "type": "connect_http"}, _KEY_FIELD)["request_id"],
                   {"secret": LLM_KEY})["status"] == "answered"
    refused = configure_connection(universe_id=UID, payload=json.dumps({
        "destination": "quillmind", "uses": QUILLMIND_ASK["uses"],
    }))
    assert refused["error"] == "connection_setup_invalid"
    assert "owner's confirmation" in refused["detail"]
    rows = cloud_connections(action="list", universe_id=UID)["connections"]
    (row,) = [r for r in rows if r["destination"] == "quillmind"]
    assert "model" not in row["uses"]


def test_configure_cannot_change_the_billing_of_an_owner_confirmed_model_use(owner):
    from tinyassets.api.connection_uses import configure_connection

    answered = _answer(_ask(QUILLMIND_ASK, _KEY_FIELD)["request_id"], {"secret": LLM_KEY})
    assert answered["status"] == "answered", answered
    flat = {**QUILLMIND_ASK["uses"]["model"], "billing": "flat"}
    refused = configure_connection(universe_id=UID, payload=json.dumps({
        "destination": "quillmind", "uses": {"model": flat},
    }))
    assert "owner's confirmation" in refused["detail"]
    # Re-stating exactly what the owner confirmed is a harmless no-op.
    same = configure_connection(universe_id=UID, payload=json.dumps({
        "destination": "quillmind", "uses": QUILLMIND_ASK["uses"],
    }))
    assert same["status"] == "configured", same


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
    ledger = ConnectionLedger(base / "outbound.db")
    grant = ledger.get_grant(_ids(universe_id=UID, destination="priced")[1])
    ledger.configure_capability(
        connection_id=grant.connection_id, capability_kind="model_discovery",
        descriptor=DESCRIPTOR, enabled=True, expected_grant=grant,
    )
    return ledger, grant


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


def test_the_catalogues_prices_keep_deciding_spend_even_beside_a_declaration(owner):
    """Even a declaration that reached storage (an older write, a race) never
    replaces the catalogue: discovery reads the priced source, so the owner's
    free-only access and spend caps are enforced against real prices."""
    import sqlite3

    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.discovery_snapshot import _context
    from tinyassets.storage.outbound_connections import (
        ModelDiscoveryCapability,
        ModelUseCapability,
    )

    _ledger, grant = _priced_connection(owner)
    with sqlite3.connect(owner / "outbound.db") as conn:
        conn.execute(
            "INSERT INTO connection_capabilities VALUES (?, 'model_use', ?, 0)",
            (grant.connection_id, json.dumps(PAID_AS_FREE["model"])),
        )
    definition = register_definition(
        universe_id=UID, owner_user_id=OWNER, access_method="api_key_http",
        protocol="openai_chat", model="seed", ref=grant.grant_id,
    )
    profile = _context(owner, OWNER, UID, definition.id).profile
    assert isinstance(profile, ModelDiscoveryCapability)
    assert not isinstance(profile, ModelUseCapability)


def test_a_declaration_cannot_replace_accepted_spending_limits(owner, monkeypatch):
    from types import SimpleNamespace

    from tinyassets.api.connection_uses import apply_connection_uses
    from tinyassets.api.http_connection import _ids
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers.definition import register_definition

    answered = _answer(_ask({**PRICED_ASK, "destination": "capped"}, _KEY_FIELD)[
        "request_id"], {"secret": LLM_KEY})
    assert answered["status"] == "answered", answered
    grant_id = _ids(universe_id=UID, destination="capped")[1]
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


def test_the_owner_is_told_the_billing_is_the_requesters_claim(owner):
    sentence = _ask(QUILLMIND_ASK, _KEY_FIELD)["grant_sentence"]
    assert "nothing is spent" not in sentence
    assert "The requester declared these free of charge" in sentence
    assert "cannot check" in sentence and "billed to your account" in sentence


# --------------------------------------------------------------------------- #
# A constant header can never shadow or duplicate the key header.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["X-Api-Key", "api-key", "X-Auth-Token", "X-Session"])
def test_credential_header_names_cannot_be_constants(name):
    from tinyassets.api.connection_uses import ConnectionUseError, validate_constant_headers

    with pytest.raises(ConnectionUseError, match="names a credential"):
        validate_constant_headers({name: "abc"})


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
        )
    finally:
        server.shutdown()
        server.server_close()
    assert seen == [["real-vault-key"]]


# --------------------------------------------------------------------------- #
# No vendor code: the platform never names either provider.
# --------------------------------------------------------------------------- #


def test_neither_vendor_is_named_anywhere_in_the_platform():
    root = Path(__file__).resolve().parents[1] / "tinyassets"
    hits = [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".json", ".md", ".html", ".js", ".txt"}
        and any(name in path.read_text(encoding="utf-8", errors="ignore").lower()
                for name in _VENDORS)
    ]
    assert hits == []


# --------------------------------------------------------------------------- #
# The app agent reaches configure on the served surface, as the owner only.
# --------------------------------------------------------------------------- #


@pytest.fixture
def served(monkeypatch):
    from tinyassets import engine_mcp_server as engine

    monkeypatch.setattr(engine, "_ACTOR_ID", "owner-setup")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-setup")
    monkeypatch.setattr(engine, "_engine_run_admit", lambda **kw: True)
    from tests.engine_authority_helpers import mock_engine_admission
    mock_engine_admission(monkeypatch, {"u-setup"})
    return engine


def test_served_configure_runs_as_the_owner_on_the_bound_universe(served, monkeypatch):
    from tinyassets.auth.middleware import current_identity

    seen = []

    def configure(**kwargs):
        seen.append((kwargs, current_identity().user_id))
        return {"status": "configured"}

    monkeypatch.setattr("tinyassets.api.connection_uses.configure_connection", configure)
    before = current_identity()
    document = json.dumps({"destination": "tasklark", "constant_headers": {"X-V": "1"}})
    result = json.loads(served.write_graph(target="connection", operation="configure",
                                           payload_json=document))
    assert result == {"status": "configured"}
    assert seen == [({"universe_id": "u-setup", "payload": document}, "owner-setup")]
    assert current_identity() == before
