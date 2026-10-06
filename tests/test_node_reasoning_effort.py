"""Per-node reasoning_effort — a REAL provider setting (not a prompt hint).

A branch builder can set each node's effort level (like model_hint), and it
threads through ModelConfig to the provider subprocess (Codex
``-c model_reasoning_effort=<v>``), so a light node (localize) runs minimal/low
fast+cheap and a hard node runs high. Covers: the ModelConfig field, the Codex
flag mapping, NodeDefinition round-trip, update_node set+validate, and the
end-to-end threading from a compiled node into the provider call.
"""
from __future__ import annotations

import importlib
import json

import pytest

from tinyassets.branches import NodeDefinition
from tinyassets.providers.base import ModelConfig
from tinyassets.providers.codex_provider import _reasoning_effort_args


def test_modelconfig_carries_reasoning_effort():
    assert ModelConfig().reasoning_effort == ""
    assert ModelConfig(reasoning_effort="low").reasoning_effort == "low"


@pytest.mark.parametrize("effort,expected", [
    ("low", ["-c", "model_reasoning_effort=low"]),
    ("MINIMAL", ["-c", "model_reasoning_effort=minimal"]),
    ("high", ["-c", "model_reasoning_effort=high"]),
    ("", []),
    (None, []),
    ("bogus", []),
])
def test_codex_effort_flag_mapping(effort, expected):
    assert _reasoning_effort_args(effort) == expected


def test_node_definition_round_trips_effort():
    node = NodeDefinition(node_id="n", display_name="N", reasoning_effort="minimal")
    assert node.reasoning_effort == "minimal"
    again = NodeDefinition.from_dict(node.to_dict())
    assert again.reasoning_effort == "minimal"


def test_compiled_node_threads_effort_into_provider_call(monkeypatch):
    """The decisive test: a node's reasoning_effort reaches the provider call's
    ModelConfig (not a prompt suggestion)."""
    from types import SimpleNamespace

    from tinyassets import graph_compiler
    from tinyassets.graph_compiler import _build_prompt_template_node

    # Model zero worker wait for config threading; queue-wait subtraction is
    # covered with real clocks in test_node_timeout_queue_cancellation.py.
    # Replace only this module's clock reference, not shared time.monotonic.
    monkeypatch.setattr(graph_compiler, "time", SimpleNamespace(
        monotonic=lambda: 100.0,
        sleep=graph_compiler.time.sleep,
    ))

    captured: dict = {}

    def fake_provider_call(prompt, system, *, role="writer", config=None):
        captured["config"] = config
        captured["role"] = role
        return json.dumps({"out": "done"})

    node = NodeDefinition(
        node_id="localize",
        display_name="Localize",
        prompt_template="Do {x}.",
        input_keys=["x"],
        output_keys=["out"],
        reasoning_effort="minimal",
        timeout_seconds=45.0,
    )
    fn = _build_prompt_template_node(node, provider_call=fake_provider_call, event_sink=None)
    result = fn({"x": "thing"})

    assert result.get("out")
    cfg = captured.get("config")
    assert cfg is not None, "node config was not threaded to the provider"
    assert cfg.reasoning_effort == "minimal"
    # The node's own timeout threads too (closes the node/provider decoupling).
    assert cfg.timeout == 45
    assert cfg.absolute_cap_s == 45.0


def test_subsecond_node_timeout_floors_provider_timeout_to_one():
    """Codex review fix: a sub-second node timeout must not become provider
    timeout 0 (int(0.5)==0 → instant provider timeout)."""
    from tinyassets.graph_compiler import _build_prompt_template_node

    captured: dict = {}

    def fake_provider_call(prompt, system, *, role="writer", config=None):
        captured["config"] = config
        return json.dumps({"out": "ok"})

    node = NodeDefinition(
        node_id="fast", display_name="Fast",
        prompt_template="Go {x}.", input_keys=["x"], output_keys=["out"],
        timeout_seconds=0.5,
    )
    fn = _build_prompt_template_node(node, provider_call=fake_provider_call, event_sink=None)
    fn({"x": "now"})
    assert captured["config"].timeout >= 1


def test_policy_path_skips_config_for_legacy_4arg_router():
    """Codex review fix: a router whose call_with_policy_sync takes only
    (role, prompt, system, policy) must NOT receive a 5th config arg."""
    from tinyassets.graph_compiler import _call_policy_router_with_retry

    class _LegacyRouter:
        def call_with_policy_sync(self, role, prompt, system, policy):
            return ("text", "legacy", {})

    from tinyassets.providers.base import ModelConfig
    # Should not raise TypeError despite a non-None config.
    out = _call_policy_router_with_retry(
        _LegacyRouter(), role="writer", prompt="p", system="",
        policy={"preferred": {}}, config=ModelConfig(reasoning_effort="low"),
    )
    assert out == ("text", "legacy", {})


@pytest.fixture
def server_env(tmp_path, monkeypatch, authenticate_request):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "founder")
    # `UNIVERSE_SERVER_USER` alone does not reach branch WRITE actions:
    # they resolve the caller via `_request_branch_actor()`, which is
    # credential-derived and documented "never an env actor". Without a
    # bound request subject they return "Authenticated branch subject
    # required." before parsing anything, and every later assertion dies on
    # a KeyError that hides the real cause.
    authenticate_request("founder")
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us
    importlib.reload(us)


def _basic_spec(name="Effort branch"):
    return {
        "name": name,
        "entry_point": "ready",
        "node_defs": [{
            "node_id": "ready",
            "display_name": "Ready",
            "prompt_template": "Do the work.",
        }],
        "edges": [{"from": "START", "to": "ready"}, {"from": "ready", "to": "END"}],
        "state_schema": [{"name": "x", "type": "str"}],
    }


def test_update_node_sets_and_validates_reasoning_effort(server_env):
    us = server_env
    built = json.loads(us._extensions_impl(
        action="build_branch", spec_json=json.dumps(_basic_spec()),
    ))
    bid = built["branch_def_id"]

    # Valid: set the node to low effort.
    low_op = [{"op": "update_node", "node_id": "ready", "reasoning_effort": "low"}]
    ok = json.loads(us._extensions_impl(
        action="patch_branch", branch_def_id=bid,
        changes_json=json.dumps(low_op),
    ))
    assert ok.get("status") != "rejected", ok
    got = json.loads(us._extensions_impl(action="get_branch", branch_def_id=bid))
    ready = next(n for n in got["node_defs"] if n["node_id"] == "ready")
    assert ready["reasoning_effort"] == "low"

    # Invalid: rejected with a clear error.
    bad_op = [{"op": "update_node", "node_id": "ready", "reasoning_effort": "turbo"}]
    bad = json.loads(us._extensions_impl(
        action="patch_branch", branch_def_id=bid,
        changes_json=json.dumps(bad_op),
    ))
    assert bad.get("status") == "rejected" or "error" in bad, bad
