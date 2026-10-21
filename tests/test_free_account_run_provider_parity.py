"""A universe's own ``api_key_http`` source serves its RUNS, not only its chat.

Live 2026-09-30, prod sha 6235666b, universe ``u-01ky3zh1arr8qth8jee7zx63pq``
(a free account whose only source is an OpenRouter API key). Its chat turns ran
on that source. Its workflow did not: branch ``04521fb54792`` (one ``prompt``
node, no ``llm_policy``) failed twice at the node -- runs ``61184d8f21724915``
and ``4828ae18e2414e77`` -- with ``ProviderAuthorityHeldError`` and the words
*"Connect your provider before running this command center"*, which was both the wrong
cause and the wrong fix: the provider WAS connected, ready, serving and in the
accepted manifest.

The forensic difference between the two surfaces was which code answered *"which
model?"* for an unspecified node:

* chat ordered the account's FRESH catalogue (``prepare_owned_model_plan``) and
  ran whatever was eligible;
* a run with no saved model preference built no order at all and fell through to
  ``snapshot.default_model_id or definition.model`` -- the source's DECLARED
  default. That account's declared ``inclusionai/ling-3.0-flash-vl:free`` had
  left its 632-model catalogue, and OpenRouter's user-models protocol reports no
  default, so every run, automation and agent node of that universe pinned a
  model that no longer existed.

So the pin below -- a source whose declared model is absent from its own
catalogue -- is the live shape, and it is what these tests drive. One resolver
now answers for both surfaces.

Only the two remote transports are synthetic: the HTTP inference proxy and the
discovery document. The router, graph compiler, provider admission, work
receipts, automation store and the real ``ApiKeyHttpProvider`` all run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.inference_usage_helpers import accounting_resolver
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.provider_assignment_manifest import ModelAccess

pytestmark = pytest.mark.usefixtures("cloud_runtime")

A_OWNER, A_HOME = "acct_alice", "universe_alice"
B_OWNER, B_HOME = "acct_bob", "universe_bob"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)

#: The definition's DECLARED model. Deliberately absent from every catalogue
#: below: that absence is the live defect's precondition.
DECLARED = "vendor/retired-model:free"
#: What the account can actually run right now, best first.
LIVE_MODELS = ("vendor/live-flagship:free", "vendor/live-small:free")
#: Models the account's key COULD reach but the owner never accepted paying for.
#: A connected key alone must not let a workflow spend on them.
PRICED_MODELS = ("vendor/paid-flagship", "vendor/paid-small")


def _catalogue_entry(model_id: str) -> dict[str, Any]:
    price = "0" if model_id.endswith(":free") else "0.000003"
    return {
        "id": model_id,
        "canonical_slug": model_id,
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools"],
        "context_length": 128000,
        "pricing": {"prompt": price, "completion": price, "image": "0", "request": "0"},
    }


class _Wire:
    """One synthetic remote source: the model catalogue plus the chat endpoint."""

    def __init__(self, *, models: tuple[str, ...] = LIVE_MODELS) -> None:
        self.models = models
        self.reads: list[dict[str, Any]] = []
        self.requests: list[tuple[str, dict[str, Any]]] = []
        #: Models this source answers 429 for, as OpenRouter does when a FREE
        #: model's own window is spent. Everything else answers normally.
        self.rate_limited: frozenset[str] = frozenset()
        #: Models this source answers 5xx for. A gateway 5xx may follow upstream
        #: generation, so it is NOT a proven side-effect-free refusal.
        self.overloaded: frozenset[str] = frozenset()

    # -- discovery -------------------------------------------------------
    def read(self, **kwargs: Any) -> dict[str, Any]:
        self.reads.append(kwargs)
        return {"data": [_catalogue_entry(model) for model in self.models]}

    # -- inference -------------------------------------------------------
    def close(self) -> None:
        pass

    def request(self, verb: str, document: dict[str, Any]) -> dict[str, Any]:
        self.requests.append((verb, document))
        model = document["body"]["model"]
        if model in self.rate_limited:
            # A whole-response status, exactly as the live source sends it: a
            # header-less 429 with no body, before a single token is generated.
            return {"status": 429, "headers": {}, "body": ""}
        if model in self.overloaded:
            return {"status": 503, "headers": {}, "body": ""}
        return {
            "status": 200,
            "body": json.dumps({
                "model": model,
                "choices": [{
                    "message": {"role": "assistant", "content": "morning focus note"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "cost": 0},
            }),
        }

    @property
    def sent_models(self) -> list[str]:
        return [document["body"]["model"] for _verb, document in self.requests]


@pytest.fixture
def wires(tmp_path, monkeypatch):
    """Per-universe synthetic transports, routed by the owner the call carries.

    Routing on ``owner_user_id`` / the grant is what makes the cross-user test
    an observation rather than an assumption: if universe B's run reached A's
    source, it would land in A's wire and be counted there.
    """
    from tinyassets.providers import discovery_snapshot
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider

    registry: dict[str, _Wire] = {}

    def read(**kwargs: Any) -> dict[str, Any]:
        return registry[kwargs["owner_user_id"]].read(**kwargs)

    def resolve_proxy(self, **_kwargs: Any) -> _Wire:
        return registry[self._definition.owner_user_id]

    # An http model is denied "engine_tools_unavailable" without this, so the
    # plan would have no eligible candidate for a reason unrelated to the
    # subject under test.
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    # The router and its cooldown map are PROCESS globals. Now that a spent
    # source is cooled after the fact, a cooldown one test writes would skip the
    # source in the next one -- and a skipped source sends no request at all,
    # which reads as a fix that stopped working. Give each test its own router.
    from tinyassets.providers import call as call_module
    from tinyassets.providers.router import ProviderRouter

    monkeypatch.setattr(call_module, "_router", ProviderRouter(), raising=False)
    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", read)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", accounting_resolver(resolve_proxy))
    return registry


def _seed_universe(
    tmp_path,
    monkeypatch,
    wires,
    *,
    owner: str,
    universe: str,
    suffix: str,
    models: tuple[str, ...] = LIVE_MODELS,
) -> str:
    """One owner, one home, one accepted ``api_key_http`` source. No preference.

    Deliberately NO saved model preference: that is the free account's state
    (it never opened a model picker) and the state the live defect needed.
    """
    from tests.support.broker_double import seed_owner_admission
    from tinyassets.commons_catalog import create_binding, publish_definition
    from tinyassets.daemon_server import (
        grant_universe_access,
        grant_universe_ownership,
        initialize_author_server,
        set_founder_home,
    )
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    seed_owner_admission(tmp_path, principal=owner, center=universe)
    # Registered FIRST: `set_serving` below performs real discovery readiness
    # against this owner's source, so the transport has to exist by then.
    wires[owner] = _Wire(models=models)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    initialize_author_server(tmp_path)
    universe_dir = tmp_path / universe
    universe_dir.mkdir(exist_ok=True)
    grant_universe_ownership(tmp_path, universe_id=universe, owner_id=owner)
    grant_universe_access(tmp_path, universe_id=universe, actor_id=owner,
                          permission="admin", granted_by=owner)
    set_founder_home(tmp_path, founder_sub=owner, universe_id=universe,
                     platform_generated=True)

    connection_id = "http_" + suffix * 32
    grant_id = "http_grant_" + suffix * 32
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: owner,
    )
    ledger.create_connection(
        connection_id=connection_id, owner_user_id=owner, connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("GET", "POST"),
        provider="http", destination="compute:synthetic",
        credential_ref="vault://http/compute:synthetic",
        allowed_endpoints=[
            {"host": "api.example.com", "path_template": "/v1/chat/completions",
             "methods": ["POST"]},
            {"host": "api.example.com", "path_template": "/api/v1/models/user",
             "methods": ["GET"], "allowed_query": ["output_modalities"],
             "required_query": ["output_modalities"],
             "query_patterns": {"output_modalities": "^all$"}},
        ],
    )
    ledger.grant_connection(
        grant_id=grant_id, connection_id=connection_id, owner_user_id=owner,

        unprompted_action_cap=ActionCap("http_requests", 500, "requests"),
    )
    definition = register_definition(
        universe_id=universe, owner_user_id=owner, access_method="api_key_http",
        protocol="openai_chat", model=DECLARED, ref=grant_id,
    )
    ledger.configure_capability(
        connection_id=connection_id, capability_kind="model_discovery", enabled=True,
        descriptor={
            "protocol": "openrouter_user_models_v1",
            "catalogue_url":
                "https://api.example.com/api/v1/models/user?output_modalities=all",
            "benchmark_url": "",
        },
        expected_grant=ledger.get_grant(grant_id),
    )
    published = publish_definition(
        tmp_path, author_id=owner,
        payload={
            "schema_version": 1, "name": f"{universe} agent",
            "description": "Open-source run parity fixture.", "tags": ["test"],
            "components": {"identity": {"kind": "soul", "config": {}}},
        },
    )
    agent = create_binding(
        tmp_path, universe_id=universe, definition_id=published["agent_definition_id"],
        created_by=owner, payload={"schema_version": 1, "name": f"{universe} agent",
                                   "role": "writer"},
    )
    connected = bind_serving_provider(
        base_path=tmp_path, universe_dir=universe_dir, owner_user_id=owner,
        universe_id=universe, agent_binding_id=agent["agent_binding_id"],
        expected_revision=1, provider=definition.id,
        model_access={definition.id: ModelAccess("discovered")},
    )
    set_serving(
        base_path=tmp_path, universe_dir=universe_dir, owner_user_id=owner,
        universe_id=universe, agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"], enabled=True,
    )
    return f"api_key_http:{definition.id}"


def _definition_id(universe: str, owner: str) -> str:
    """This universe's one registered source, read back from the real registry."""
    from tinyassets.providers.definition import list_definitions

    matches = [
        item for item in list_definitions(universe)
        if item.owner_user_id == owner and item.access_method == "api_key_http"
    ]
    assert len(matches) == 1, matches
    return matches[0].id


def _branch(
    *, owner: str, branch_def_id: str = "branch_morning_focus", agent_node: bool = False,
    visibility: str = "private",
) -> BranchDefinition:
    """The live branch shape: one prompt node with NO llm_policy pin."""
    node = NodeDefinition(
        node_id="generate_note",
        display_name="Generate note",
        prompt_template="Write a short morning focus note.",
        output_keys=["note"],
        model_hint="writer",
        tools_allowed=["universe_self"] if agent_node else [],
    )
    return BranchDefinition(
        branch_def_id=branch_def_id,
        name="Morning Focus Note",
        author=owner,
        visibility=visibility,
        graph_nodes=[GraphNodeRef(id=node.node_id, node_def_id=node.node_id)],
        edges=[
            EdgeDefinition(from_node="START", to_node=node.node_id),
            EdgeDefinition(from_node=node.node_id, to_node="END"),
        ],
        entry_point=node.node_id,
        node_defs=[node],
        state_schema=[{"name": "note", "type": "str", "default": ""}],
    )


def _bind_api(monkeypatch, tmp_path, universe: str) -> None:
    from tinyassets.api import runs as api_runs

    monkeypatch.setattr(api_runs, "_ensure_runs_recovery", lambda: None)
    monkeypatch.setattr(api_runs, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(api_runs, "_universe_dir", lambda _uid: tmp_path / universe)
    monkeypatch.setattr(api_runs, "_request_universe", lambda uid="": uid or universe)


def _run(tmp_path, monkeypatch, branch: BranchDefinition, universe: str) -> dict[str, Any]:
    """Drive `run_graph` exactly as the connector does, then read the run row."""
    from tinyassets.api import runs as api_runs
    from tinyassets.daemon_server import save_branch_definition
    from tinyassets.runs import get_run, wait_for

    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    _bind_api(monkeypatch, tmp_path, universe)
    response = json.loads(api_runs._action_run_branch(
        {"branch_def_id": branch.branch_def_id, "universe_id": universe}
    ))
    assert "run_id" in response, response
    wait_for(response["run_id"], timeout=60)
    record = get_run(tmp_path, response["run_id"])
    assert record is not None
    return record


# --------------------------------------------------------------------------
# The live defect: a prompt node with no pin, on a source whose declared model
# has left its own catalogue.
# --------------------------------------------------------------------------
























@pytest.fixture
def engine_tools(tmp_path, monkeypatch):
    """The universe's own engine-tool surface, with a synthetic MCP transport.

    An agent node needs `engine_tools_authorized`; without a route it is refused
    for a reason unrelated to model resolution. The route, the ACL and the
    admission are real; only the MCP client is synthetic.
    """
    from types import SimpleNamespace

    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

    from tinyassets import engine_mcp_http, engine_tool_client
    from tinyassets.providers.base import ModelConfig
    from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS

    calls: list[tuple[str, dict[str, Any]]] = []
    engine_mcp_http._write_routes(tmp_path, [SimpleNamespace(
        universe_id=A_HOME, owner=A_OWNER, port=8791, secret="s" * 43,
    )])
    # Persona generation is not this test's subject. The adapter overwrites
    # these deliberately wrong identities with the run's own.
    monkeypatch.setattr("tinyassets.shared_self.prepare_shared_self_turn", lambda *args, **kwargs: (
        args[3], "work system", ModelConfig(
            engine_mcp_enabled=True, engine_mcp_actor_id="wrong-owner",
            engine_mcp_graph_id="wrong-universe", max_tokens=2048, absolute_cap_s=120,
        ),
    ))

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def is_connected(self):
            return True

        async def list_tools_mcp(self, *, cursor=None):
            return ListToolsResult(tools=[
                Tool(name=name, inputSchema={"type": "object"})
                for name in SERVED_ENGINE_MCP_TOOLS
            ])

        async def call_tool_mcp(self, name, arguments):
            calls.append((name, arguments))
            return CallToolResult(
                content=[TextContent(type="text", text="known work result")]
            )

    def make_client(route, timeout):
        assert route.actor_id == A_OWNER and route.graph_id == A_HOME
        return Client()

    monkeypatch.setattr(engine_tool_client, "_make_client", make_client)
    return calls






# --------------------------------------------------------------------------
# Cross-user: resolving from the fresh catalogue must not widen whose source a
# run may reach.
# --------------------------------------------------------------------------










# --------------------------------------------------------------------------
# A held run must say what is missing.
# --------------------------------------------------------------------------










# --------------------------------------------------------------------------
# `__system__` is not a node.
# --------------------------------------------------------------------------






