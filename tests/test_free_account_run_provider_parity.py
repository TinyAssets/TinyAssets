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
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
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
from tinyassets.storage.provider_work_authority import db_path as authority_db_path

pytestmark = pytest.mark.usefixtures("cloud_runtime")

A_OWNER, A_HOME = "acct_alice", "universe_alice"
B_OWNER, B_HOME = "acct_bob", "universe_bob"
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)

#: The definition's DECLARED model. Deliberately absent from every catalogue
#: below: that absence is the live defect's precondition.
DECLARED = "vendor/retired-model:free"
#: What the account can actually run right now, best first.
LIVE_MODELS = ("vendor/live-flagship:free", "vendor/live-small:free")


def _catalogue_entry(model_id: str) -> dict[str, Any]:
    return {
        "id": model_id,
        "canonical_slug": model_id,
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools"],
        "context_length": 128000,
        "pricing": {"prompt": "0", "completion": "0", "image": "0", "request": "0"},
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

    monkeypatch.setattr(call_module, "_real_router", ProviderRouter(), raising=False)
    monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", read)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", accounting_resolver(resolve_proxy))
    monkeypatch.setattr("tinyassets.providers.call._force_mock", False)
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
    from tinyassets.custom_agents import create_binding, publish_definition
    from tinyassets.daemon_server import (
        grant_universe_access,
        initialize_author_server,
        set_founder_home,
    )
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    # Registered FIRST: `set_serving` below performs real discovery readiness
    # against this owner's source, so the transport has to exist by then.
    wires[owner] = _Wire(models=models)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    initialize_author_server(tmp_path)
    universe_dir = tmp_path / universe
    universe_dir.mkdir(exist_ok=True)
    grant_universe_access(tmp_path, universe_id=universe, actor_id=owner,
                          permission="admin", granted_by=owner)
    set_founder_home(tmp_path, founder_sub=owner, universe_id=universe,
                     platform_generated=True)

    connection_id = "http_" + suffix * 32
    grant_id = "http_grant_" + suffix * 32
    ledger = ConnectionLedger(
        tmp_path / "outbound.db",
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
        universe_id=universe,
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


def test_an_unpinned_prompt_node_runs_on_a_model_the_account_actually_has(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "completed", record["error"]
    wire = wires[A_OWNER]
    assert wire.sent_models == [LIVE_MODELS[0]], wire.sent_models
    # The retired declared default is never what a run sends. Before the fix
    # this list was empty and the run failed `authority_held` instead.
    assert DECLARED not in wire.sent_models
    assert record["output"]["note"] == "morning focus note"


def test_a_free_models_429_steps_to_the_next_model_the_owner_already_accepted(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """The second live report: one 429 ended the whole run.

    Live 2026-09-30 ~05:16Z on deployed `704b066d`, run `c22c1cb12db74d6a`:

        work model attempt is held: qwen/qwen3.8-27b:free on
        api_key_http:provdef_ed01... (provider_rate_limited)

    with `provider_chain.attempts` holding exactly ONE attempt, on an account
    with 632 models whose chat turns fall through to another free model on the
    same 429. The recorded diagnostic carried
    `detail: "compute provider rate limited (429)"` -- the source's own HTTP
    status, NOT `_MAX_BINDING_INVOCATIONS` or any other platform limit -- with
    no `capacity_scope`, no `retry_after_s` and no `side_effect_state`, because
    the provider only decodes a capacity signal for an AGENT round.

    So a workflow node must step to the next model in the owner's own captured
    order, which is the same fallback a chat turn takes.
    """
    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    wires[A_OWNER].rate_limited = frozenset({LIVE_MODELS[0]})

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "completed", record["error"]
    assert wires[A_OWNER].sent_models == list(LIVE_MODELS), wires[A_OWNER].sent_models
    assert record["output"]["note"] == "morning focus note"


def test_every_model_rate_limited_is_exhaustion_naming_each_one(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """Stepping past a 429 must still stop, and say what it tried.

    The bound is the owner's own order: when every model in it answered 429 the
    run reports typed exhaustion naming each, not "held" after the first.
    """
    from tinyassets.exceptions import WorkModelExhaustedError

    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    wires[A_OWNER].rate_limited = frozenset(LIVE_MODELS)

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "failed"
    assert WorkModelExhaustedError.MESSAGE in record["error"], record["error"]
    assert wires[A_OWNER].sent_models == list(LIVE_MODELS), wires[A_OWNER].sent_models
    for model in LIVE_MODELS:
        assert model in record["error"], record["error"]
    assert "provider_rate_limited" in record["error"], record["error"]


def test_a_spent_source_is_cooled_once_the_nodes_order_runs_out(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """The withheld cooldown is a debt this node has to settle.

    The router withholds a source's cooldown so the sibling attempt is not
    skipped by its own quota gate. That purchase ends when the order runs out,
    and a source at a DAILY cap -- which refuses every model -- would otherwise
    have every later run pay the whole order again, forever. The conversation
    path settles it in `_cool_abandoned_source`; a workflow node had nothing.
    """
    from tinyassets.providers import call as call_module

    authenticate_request(A_OWNER)
    provider = _seed_universe(
        tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a",
    )
    wires[A_OWNER].rate_limited = frozenset(LIVE_MODELS)

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "failed"
    router = call_module.get_provider_router()
    assert router is not None
    assert router._quota.cooldown_remaining(provider, owner=A_OWNER) > 0, (
        "a source that refused every model was left hot"
    )


def test_a_source_that_still_has_a_sibling_is_not_cooled_mid_node(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """Cooling too early is the dead end: it skips the very sibling that works."""
    from tinyassets.providers import call as call_module

    authenticate_request(A_OWNER)
    provider = _seed_universe(
        tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a",
    )
    wires[A_OWNER].rate_limited = frozenset({LIVE_MODELS[0]})

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "completed", record["error"]
    router = call_module.get_provider_router()
    assert router is not None
    assert router._quota.cooldown_remaining(provider, owner=A_OWNER) == 0


def test_a_5xx_does_not_replay_the_node_on_another_model(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """A gateway 5xx is not proof that nothing was generated.

    Codex refutation R3, 2026-09-30: an earlier head declared EVERY non-2xx
    side-effect-free, and a 502/504 can come from a gateway after an upstream
    model already began producing output. Only a 4xx admission refusal proves
    zero generation, so a 5xx must hold the node rather than replay it.
    """
    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    wires[A_OWNER].overloaded = frozenset({LIVE_MODELS[0]})

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "failed"
    assert wires[A_OWNER].sent_models == [LIVE_MODELS[0]], wires[A_OWNER].sent_models
    assert LIVE_MODELS[1] not in wires[A_OWNER].sent_models


def test_a_withheld_cooldown_is_settled_even_when_the_node_raises(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """The debt does not depend on HOW the node ended.

    Codex refutation R4, 2026-09-30: settlement ran only on the exhaustion
    branch, so a node that raised between attempts -- a cancellation or an
    authority change -- left the source hot forever. Driven by making the
    SECOND authorization raise, after the first model's 429 withheld the
    cooldown.
    """
    from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
    from tinyassets.providers import call as call_module

    authenticate_request(A_OWNER)
    provider = _seed_universe(
        tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a",
    )
    wires[A_OWNER].rate_limited = frozenset({LIVE_MODELS[0]})
    original = _ForegroundRunProviderSession._authorize_attempt
    calls = []

    def authorize(self, **kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            raise PermissionError("synthetic authority change between attempts")
        return original(self, **kwargs)

    monkeypatch.setattr(_ForegroundRunProviderSession, "_authorize_attempt", authorize)

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "failed"
    assert len(calls) == 2, calls
    router = call_module.get_provider_router()
    assert router is not None
    assert router._quota.cooldown_remaining(provider, owner=A_OWNER) > 0, (
        "a node that raised between attempts left its source hot"
    )


def test_a_node_declaring_no_fallbacks_stays_on_its_pin_through_a_429(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """`fallback_chain: []` still means only this model, 429 or not."""
    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    wires[A_OWNER].rate_limited = frozenset({LIVE_MODELS[0]})
    provider = f"api_key_http:{_definition_id(A_HOME, A_OWNER)}"
    branch = _branch(owner=A_OWNER)
    branch.node_defs[0].llm_policy = {
        "preferred": {"provider": provider, "model": LIVE_MODELS[0]},
        "fallback_chain": [],
    }

    record = _run(tmp_path, monkeypatch, branch, A_HOME)

    assert record["status"] == "failed"
    assert wires[A_OWNER].sent_models == [LIVE_MODELS[0]], wires[A_OWNER].sent_models
    assert LIVE_MODELS[1] not in wires[A_OWNER].sent_models


def test_an_unnamed_model_resolves_at_the_reservation_even_with_no_captured_order(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """The second layer, asserted with the first one switched off.

    Two lanes reach `_validate_work_selection` with a model nobody named: the
    run session (fixed by capturing the owner's order) and the background branch
    consumer, which resolves `model_id` from the node's policy alone and has no
    captured order at all. So the enforcement point itself must not fall back to
    the source's DECLARED default either.

    Driven by disabling the captured order, which is exactly the pre-fix session
    and the background lane's shape. Without this, two layers would cover for
    each other and neither would be proven.
    """
    from tinyassets import foreground_run_provider

    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    monkeypatch.setattr(
        foreground_run_provider, "captured_work_preference", lambda *a, **k: None,
    )

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "completed", record["error"]
    assert wires[A_OWNER].sent_models == [LIVE_MODELS[0]], wires[A_OWNER].sent_models
    assert DECLARED not in wires[A_OWNER].sent_models


def test_the_reservation_never_consults_the_sources_declared_model(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """`eligible_model_ids` reads the snapshot, not the registration row.

    A registered `model` is where the run pinned a vanished id from. The
    ordering it replaced is the SAME one `_validate_snapshot` admits a named
    model against, so an unnamed model can never resolve to something the
    validator would then refuse.
    """
    from tinyassets.providers.definition import get_definition
    from tinyassets.providers.discovery_snapshot import refresh_model_discovery
    from tinyassets.providers.model_selection import _validate_snapshot, eligible_model_ids

    authenticate_request(A_OWNER)
    provider = _seed_universe(
        tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a",
    )
    definition = get_definition(A_HOME, provider.removeprefix("api_key_http:"))
    assert definition.model == DECLARED
    snapshot = refresh_model_discovery(
        owner_user_id=A_OWNER, universe_id=A_HOME, definition_id=definition.id,
    )
    assert snapshot.models.default_model_id is None

    eligible = eligible_model_ids(
        definition, snapshot, access=ModelAccess("discovered"),
    )

    assert eligible == LIVE_MODELS
    assert DECLARED not in eligible
    # What it chose is exactly what the validator accepts -- one ordering.
    selected, _recheck = _validate_snapshot(
        definition, snapshot, provider, eligible[0], ModelAccess("discovered"),
    )
    assert selected.model_id == eligible[0]
    with pytest.raises(PermissionError):
        _validate_snapshot(
            definition, snapshot, provider, DECLARED, ModelAccess("discovered"),
        )


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
    monkeypatch.setattr("tinyassets.shared_self.prepare_shared_self_turn", lambda *args: (
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


def test_an_agent_node_resolves_the_same_model_as_a_prompt_node(
    tmp_path, monkeypatch, authenticate_request, wires, engine_tools,
):
    """An agent node takes the same resolved model, not the retired pin.

    `call_foreground_work_agent` reads the session's captured order too, so the
    same missing read killed agent nodes on this account; with no order it fell
    into the same `definition.model` pin.
    """
    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")

    record = _run(
        tmp_path, monkeypatch, _branch(owner=A_OWNER, agent_node=True), A_HOME,
    )

    assert record["status"] == "completed", record["error"]
    assert wires[A_OWNER].sent_models == [LIVE_MODELS[0]], wires[A_OWNER].sent_models
    assert DECLARED not in wires[A_OWNER].sent_models


def test_a_due_automation_runs_on_the_universes_own_source(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """The background lane, on a thread with no request identity bound.

    An automation is where the live report started (cron
    ``ff08a05dbae0435bacd29353280fc591``), and it shares this session factory,
    so the fix has to hold with no ambient request actor at all.
    """
    from tinyassets.automations import AutomationStore, register_automation, run_due_automation
    from tinyassets.daemon_server import save_branch_definition

    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    branch = _branch(owner=A_OWNER)
    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    _bind_api(monkeypatch, tmp_path, A_HOME)
    automation = register_automation(
        tmp_path, universe_id=A_HOME, owner_principal_id=A_OWNER, name="morning",
        branch_def_id=branch.branch_def_id, interval_seconds=600, inputs={}, now=NOW,
    )

    outcome: dict[str, Any] = {}

    def go() -> None:
        try:
            outcome["reason"] = run_due_automation(
                tmp_path, automation, "2026-09-30T12:10:00+00:00", now=NOW,
            )
        except BaseException as exc:  # noqa: BLE001 - reported below, never swallowed
            outcome["exc"] = exc

    worker = threading.Thread(target=go)
    worker.start()
    worker.join(180)
    assert not worker.is_alive(), "the due run did not finish"
    if "exc" in outcome:
        raise outcome["exc"]

    assert outcome["reason"].startswith("ok:ran:"), outcome["reason"]
    assert wires[A_OWNER].sent_models == [LIVE_MODELS[0]], wires[A_OWNER].sent_models
    assert AutomationStore(tmp_path).get(automation.automation_id).last_run_id


# --------------------------------------------------------------------------
# Cross-user: resolving from the fresh catalogue must not widen whose source a
# run may reach.
# --------------------------------------------------------------------------


def test_universe_b_run_never_reaches_universe_as_provider(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """Two owners, two sources, two catalogues with no model in common.

    B's run may only appear in B's wire. A's wire counts every read and request,
    so a leak is observed rather than assumed -- and B's model list proves it is
    B's OWN catalogue that answered, not merely that the id was permitted.
    """
    b_models = ("vendor/bob-only:free",)
    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    authenticate_request(B_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=B_OWNER, universe=B_HOME,
                   suffix="b", models=b_models)
    a_reads_before = len(wires[A_OWNER].reads)

    record = _run(
        tmp_path, monkeypatch,
        _branch(owner=B_OWNER, branch_def_id="branch_bob_focus"), B_HOME,
    )

    assert record["status"] == "completed", record["error"]
    assert wires[B_OWNER].sent_models == [b_models[0]]
    assert wires[A_OWNER].requests == [], "B's run reached A's source"
    assert len(wires[A_OWNER].reads) == a_reads_before, "B's run read A's catalogue"
    with sqlite3.connect(authority_db_path(tmp_path)) as conn:
        universes = {
            row[0] for row in conn.execute(
                "SELECT universe_id FROM provider_work_receipts WHERE work_item_kind = 'run'"
            )
        }
    assert universes == {B_HOME}


def test_a_model_pin_admits_siblings_unless_the_node_declares_no_fallbacks(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """Exact model pins are strict; provider-only pins retain source siblings.

    Keep the historical test identity, but replace its obsolete expectation:
    omitting fallback_chain no longer permits silent model substitution. Both
    exact-pin forms must stop after exhaustion; only a source-only choice may
    advance through that source's captured model order.
    """
    from tinyassets.provider_serving_binding import resolve_serving_agent_binding
    from tinyassets.providers.model_policy import Exhaustion, ModelRef
    from tinyassets.providers.served_model_plan import prepare_owned_model_plan
    from tinyassets.providers.work_candidate_data import WorkCandidateData

    authenticate_request(A_OWNER)
    provider = _seed_universe(
        tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a",
    )
    prepared = prepare_owned_model_plan(
        base=tmp_path, universe=tmp_path / A_HOME, owner=A_OWNER,
        agent=resolve_serving_agent_binding(
            tmp_path, universe_id=A_HOME, owner_user_id=A_OWNER,
        ),
    )
    pinned, sibling = (ModelRef(provider, model) for model in LIVE_MODELS)
    snapshot = _branch(owner=A_OWNER).to_dict()

    open_pin = {"preferred": {"model": LIVE_MODELS[0]}}
    order = WorkCandidateData(prepared.plan)
    snapshot["node_defs"][0]["llm_policy"] = open_pin
    order.fit(snapshot, ceiling=10_000, retry_multiplier=1)
    assert order.next_candidate(open_pin) == pinned
    assert order.next_candidate(
        open_pin, (Exhaustion("model", pinned),),
    ) is None, "an exact model pin must never silently substitute a sibling"

    closed_pin = {"preferred": {"model": LIVE_MODELS[0]}, "fallback_chain": []}
    order = WorkCandidateData(prepared.plan)
    snapshot["node_defs"][0]["llm_policy"] = closed_pin
    order.fit(snapshot, ceiling=10_000, retry_multiplier=1)
    assert order.next_candidate(closed_pin) == pinned
    assert order.next_candidate(
        closed_pin, (Exhaustion("model", pinned),),
    ) is None, "fallback_chain: [] means only this model, and it must stay that way"

    source_pin = {"preferred": {"provider": provider}}
    order = WorkCandidateData(prepared.plan)
    snapshot["node_defs"][0]["llm_policy"] = source_pin
    order.fit(snapshot, ceiling=10_000, retry_multiplier=1)
    assert order.next_candidate(source_pin) == pinned
    assert order.next_candidate(
        source_pin, (Exhaustion("model", pinned),),
    ) == sibling, "a provider-only pin retains siblings from the same source"


def test_a_foreign_authored_branch_makes_no_discovery_request_at_all(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """A refused run must not spend the requester's own source to find out.

    Capturing the model order is credential-bearing outbound IO on the owner's
    own grant. It sat above `_admit`'s Branch-author check, so running another
    user's PUBLIC Branch made one catalogue request before the refusal -- the
    same shape as the sign-in refresh removed from this lane on #4082. Found by
    the Codex refutation of this change (C2, 2026-09-29).
    """
    from tinyassets.daemon_server import save_branch_definition

    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    # PUBLIC, because that is the only foreign Branch a requester can start --
    # and exactly the shape the refutation reproduced.
    foreign = _branch(
        owner="acct_someone_else", branch_def_id="branch_foreign", visibility="public",
    )
    save_branch_definition(tmp_path, branch_def=foreign.to_dict())
    reads_before = len(wires[A_OWNER].reads)

    record = _run(tmp_path, monkeypatch, foreign, A_HOME)

    assert record["status"] == "failed"
    assert "author is not the principal" in record["error"], record["error"]
    assert len(wires[A_OWNER].reads) == reads_before, "a refused run read the catalogue"
    assert wires[A_OWNER].requests == []


def test_universe_a_pin_is_refused_inside_universe_b(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """An explicit pin naming ANOTHER universe's source is refused, not served."""
    authenticate_request(A_OWNER)
    a_provider = _seed_universe(
        tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a",
    )
    authenticate_request(B_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=B_OWNER, universe=B_HOME,
                   suffix="b", models=("vendor/bob-only:free",))
    branch = _branch(owner=B_OWNER, branch_def_id="branch_bob_steals")
    branch.node_defs[0].llm_policy = {
        "preferred": {"provider": a_provider, "model": LIVE_MODELS[0]},
        "fallback_chain": [],
    }

    record = _run(tmp_path, monkeypatch, branch, B_HOME)

    assert record["status"] == "failed"
    assert wires[A_OWNER].requests == [], "B ran on A's source"
    assert wires[B_OWNER].requests == []


# --------------------------------------------------------------------------
# A held run must say what is missing.
# --------------------------------------------------------------------------


def test_a_held_run_names_its_own_refusal_instead_of_connect_your_provider(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """The second half of the live report: the notice named the wrong cause.

    Drives a refusal from inside admission -- the principal's home moved after
    the run was accepted -- on a universe whose provider IS connected. The
    record must carry that refusal's own words, and must not tell an owner who
    has connected a provider to connect one.
    """
    from tinyassets.daemon_server import save_branch_definition, set_founder_home
    from tinyassets.providers.owner_binding import (
        AUTHORITY_HELD_DETAIL,
        CONNECT_PROVIDER_MESSAGE,
    )

    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    branch = _branch(owner=A_OWNER)
    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    set_founder_home(tmp_path, founder_sub=A_OWNER, universe_id="universe_elsewhere",
                     platform_generated=True)

    record = _run(tmp_path, monkeypatch, branch, A_HOME)

    assert record["status"] == "failed"
    assert AUTHORITY_HELD_DETAIL in record["error"], record["error"]
    assert "not the principal's own command center" in record["error"], record["error"]
    assert CONNECT_PROVIDER_MESSAGE not in record["error"]
    assert wires[A_OWNER].requests == []


def test_a_held_run_keeps_the_provider_not_bound_failure_class(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """The precise message must not re-classify the failure.

    The stored-string taxonomy is a substring net, and a wrapped cause's words
    are arbitrary: without a key on the lead-in, a refusal mentioning "timeout"
    or "credential" would be classified as a timeout or an expired host key.
    """
    from tinyassets.api.runs import _classify_run_outcome_error
    from tinyassets.providers.owner_binding import AUTHORITY_HELD_DETAIL

    for cause in (
        "foreground run provider authority is stale",
        "discovery timed out reaching the source",
        "this universe's credential reference changed",
    ):
        annotation = _classify_run_outcome_error(AUTHORITY_HELD_DETAIL + cause)
        assert annotation is not None
        assert annotation[0] == "permission_denied:provider_not_bound", cause


def test_an_emptied_catalogue_names_the_eligibility_gap_not_a_missing_provider(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """No runnable model is a different owner problem from no provider.

    The catalogue is emptied AFTER the source is accepted, which is the only
    order this can happen in: a source with nothing runnable cannot be accepted
    for serving in the first place, and a remote catalogue changes under an
    already-accepted source -- the same way the live account's declared model
    left it.

    The plan's own refusal already names what is missing (and, when a whole
    source is held, which one and why). That wording is what the record must
    carry; the owner was previously told to connect the provider they had.
    """
    from tinyassets.providers.owner_binding import CONNECT_PROVIDER_MESSAGE
    from tinyassets.providers.served_model_plan import NO_ELIGIBLE_MODEL

    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    wires[A_OWNER].models = ()

    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)

    assert record["status"] == "failed"
    assert NO_ELIGIBLE_MODEL in record["error"], record["error"]
    assert CONNECT_PROVIDER_MESSAGE not in record["error"]
    assert wires[A_OWNER].requests == []


def test_an_order_that_runs_out_mid_run_is_typed_exhaustion_not_held_authority(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """`WorkCandidateData` with no eligible ref is exhaustion, by TYPE.

    `_admit`'s handler turns any other exception into held authority, and
    "your accepted sources produced no runnable model" is a different owner
    action from "connect a provider". Driven at the class, because the plan
    refuses one step earlier when a source has no eligible model at all.
    """
    from dataclasses import replace

    from tinyassets.exceptions import ProviderAuthorityHeldError, WorkModelExhaustedError
    from tinyassets.provider_serving_binding import resolve_serving_agent_binding
    from tinyassets.providers.model_policy import Catalog
    from tinyassets.providers.served_model_plan import prepare_owned_model_plan
    from tinyassets.providers.work_candidate_data import WorkCandidateData

    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    prepared = prepare_owned_model_plan(
        base=tmp_path, universe=tmp_path / A_HOME, owner=A_OWNER,
        agent=resolve_serving_agent_binding(
            tmp_path, universe_id=A_HOME, owner_user_id=A_OWNER,
        ),
    )
    assert prepared is not None
    # The same plan with nothing left in its catalogue. `source_policies` goes
    # with it: the plan validates that the two describe the same sources.
    empty = replace(
        prepared.plan, catalog=Catalog(A_OWNER, A_HOME, ()), source_policies=(),
    )

    with pytest.raises(WorkModelExhaustedError) as caught:
        WorkCandidateData(empty)

    assert str(caught.value) == WorkModelExhaustedError.MESSAGE
    # A subclass on purpose: every existing held handler keeps catching it.
    assert isinstance(caught.value, ProviderAuthorityHeldError)


# --------------------------------------------------------------------------
# `__system__` is not a node.
# --------------------------------------------------------------------------


def test_the_system_row_is_not_reported_as_a_node_of_the_run(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """`__system__: recursion_limit_applied` read as a workflow step.

    Live 2026-09-30: it appeared in the run summary's node list and in the
    mermaid graph, which both a user and a chatbot read as a failed step. The
    applied limit is still reported -- as the run's own `recursion_limit` field.
    """
    from tinyassets.api import runs as api_runs
    from tinyassets.runs import SYSTEM_EVENT_NODE_ID, list_events

    authenticate_request(A_OWNER)
    _seed_universe(tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a")
    record = _run(tmp_path, monkeypatch, _branch(owner=A_OWNER), A_HOME)
    assert record["status"] == "completed", record["error"]

    events = list_events(tmp_path, record["run_id"], since_step=-1)
    assert any(
        event["node_id"] == SYSTEM_EVENT_NODE_ID
        and event["status"] == "recursion_limit_applied"
        for event in events
    ), "the fixture must still emit the system row this test filters"

    snapshot = api_runs._compose_run_snapshot(record, events)

    assert [status["node_id"] for status in snapshot["node_statuses"]] == ["generate_note"]
    assert SYSTEM_EVENT_NODE_ID not in snapshot["summary"]
    assert SYSTEM_EVENT_NODE_ID not in snapshot["mermaid"]
    assert "recursion_limit_applied" not in snapshot["summary"]
    # The fact itself is not lost, only moved off the node list.
    assert snapshot["recursion_limit"] == 1_000_000


def test_a_declared_system_node_id_still_never_becomes_a_node_status():
    """A graph_node literally named `__system__` cannot smuggle one back in.

    `declared_order` seeds the status map before any event is read, so filtering
    only the event loop would have left a declared row behind.
    """
    from tinyassets.runs import SYSTEM_EVENT_NODE_ID, build_node_status_map

    statuses = build_node_status_map(
        [
            {"node_id": SYSTEM_EVENT_NODE_ID, "status": "recursion_limit_applied"},
            {"node_id": "n1", "status": "ran"},
        ],
        [SYSTEM_EVENT_NODE_ID, "n1"],
    )

    assert statuses == [{"node_id": "n1", "status": "ran"}]


def test_every_run_captures_the_owners_model_order_even_with_nothing_saved(
    tmp_path, monkeypatch, authenticate_request, wires,
):
    """The seam the fix lives on, asserted directly.

    `captured_work_preference` returning None for an owner who has saved no
    preference is what left the run on the declared-model pin. A document with
    `saved: None` is the automatic mode a chat turn uses, and the session must
    build the SAME order from it.
    """
    from tinyassets.foreground_run_provider import (
        captured_work_preference,
        new_foreground_run_provider_session,
    )
    from tinyassets.storage.model_preferences import ModelPreferenceStore

    authenticate_request(A_OWNER)
    provider = _seed_universe(
        tmp_path, monkeypatch, wires, owner=A_OWNER, universe=A_HOME, suffix="a",
    )
    assert ModelPreferenceStore(tmp_path).get(A_OWNER, A_HOME).policy is None

    document = captured_work_preference(
        tmp_path, universe_id=A_HOME, principal_id=A_OWNER,
    )
    assert document == {
        "version": 1, "saved": None, "observed_generation": 0, "current": None,
    }

    session = new_foreground_run_provider_session(
        tmp_path, universe_id=A_HOME, principal_id=A_OWNER,
        provider_call=lambda *args, **kwargs: "",
    )
    session._capture_choices()
    order = session._work_candidates
    assert order is not None, "a run with nothing saved built no model order"
    assert order.automatic is True
    assert [ref.model_id for ref in order.order] == list(LIVE_MODELS)
    assert {ref.connection_id for ref in order.order} == {provider}


def test_a_legacy_assignment_without_a_manifest_keeps_its_existing_path(
    tmp_path, monkeypatch, authenticate_request,
):
    """Always returning a document must not change a legacy universe.

    `prepare_owned_model_plan` reports no plan for a no-manifest assignment, and
    `prepare_captured_choices` passes that through as no captured choices -- so
    the pre-existing single-provider path is still what a legacy universe takes.
    Seeded through the real legacy route (`model_access=None`), not by editing a
    stored row: a hand-blanked `manifest_digest` is refused as an invalid
    assignment digest, which would prove nothing about this branch.
    """
    from tests.test_run_provider_session import _seed_serving_assignment
    from tinyassets.daemon_server import initialize_author_server, set_founder_home
    from tinyassets.foreground_run_provider import captured_work_preference
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.providers.work_candidate_data import prepare_captured_choices

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    authenticate_request(A_OWNER)
    initialize_author_server(tmp_path)
    set_founder_home(tmp_path, founder_sub=A_OWNER, universe_id=A_HOME,
                     platform_generated=True)
    _seed_serving_assignment(tmp_path, model_access=None)
    assignment = load_provider_assignment(tmp_path, universe_id=A_HOME)
    assert assignment is not None and not assignment.manifest_digest

    document = captured_work_preference(
        tmp_path, universe_id=A_HOME, principal_id=A_OWNER,
    )
    assert document is not None and document["saved"] is None

    assert prepare_captured_choices(
        Path(tmp_path), owner=A_OWNER, universe=A_HOME, document=document,
    ) is None
