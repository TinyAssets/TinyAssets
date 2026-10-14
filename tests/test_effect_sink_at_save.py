"""An effect name that the run would call an unknown sink is refused at save."""
from __future__ import annotations

import sys
import types

from tinyassets.api.branches import _apply_node_spec, _apply_node_updates
from tinyassets.branches import BranchDefinition, NodeDefinition
from tinyassets.effectors import effect_declaration_error


def _authoring_imports() -> None:
    """The save helpers import the extensions module for the phase list.

    A checkout without fastmcp cannot import that module. The sink check
    does not use it, so a stand-in that answers the phase list and the
    standalone-node scan is enough to reach the refusal.
    """
    name = "tinyassets.api.extensions"
    try:
        module = __import__(name, fromlist=["VALID_PHASES", "_load_nodes"])
        if hasattr(module, "VALID_PHASES") and hasattr(module, "_load_nodes"):
            return
    except ModuleNotFoundError:
        pass
    from tinyassets.phase_vocab import VALID_PHASES

    sys.modules.pop(name, None)
    stand_in = types.ModuleType(name)
    stand_in.VALID_PHASES = VALID_PHASES
    stand_in._load_nodes = lambda: []
    sys.modules[name] = stand_in


_authoring_imports()


def _node(**kwargs) -> NodeDefinition:
    base = {
        "node_id": "judge",
        "display_name": "Judge",
        "source_code": "def run(state):\n    return {}\n",
        "output_keys": ["seen"],
    }
    base.update(kwargs)
    return NodeDefinition(**base)


def test_a_known_sink_is_not_an_error() -> None:
    assert effect_declaration_error([]) == ""
    assert effect_declaration_error(["workspace"]) == ""
    assert effect_declaration_error(["wiki_write_back"]) == ""
    assert effect_declaration_error(["authenticated_external_call"]) == ""


def test_an_unknown_sink_is_named_with_the_registry() -> None:
    err = effect_declaration_error(["github_pull_request"])
    assert err.startswith("unknown effect sink:")
    assert "github_pull_request" in err
    assert "workspace" in err
    assert "authenticated_external_call" in err


def test_update_node_refuses_an_unknown_sink_and_keeps_the_old_list() -> None:
    node = _node(effects=["workspace"])
    err = _apply_node_updates(node, {"effects": ["github_pull_request"]})
    assert "unknown effect sink" in err
    assert node.effects == ["workspace"]


def test_update_node_stores_a_registered_sink() -> None:
    node = _node()
    assert _apply_node_updates(node, {"effects": ["wiki_write_back"]}) == ""
    assert node.effects == ["wiki_write_back"]
    assert _apply_node_updates(node, {"effects": []}) == ""
    assert node.effects == []


def test_add_node_refuses_an_unknown_sink_before_the_node_is_stored() -> None:
    branch = BranchDefinition(name="score")
    err = _apply_node_spec(branch, {
        "node_id": "judge",
        "display_name": "Judge",
        "source_code": "def run(state):\n    return {}\n",
        "author": "tester",
        "effects": ["not_a_sink"],
    })
    assert "unknown effect sink" in err
    assert branch.node_defs == []


def test_add_node_stores_a_registered_sink(monkeypatch) -> None:
    monkeypatch.setattr(
        "tinyassets.api.branches.ledger_actor", lambda: "tester",
    )
    branch = BranchDefinition(name="score")
    err = _apply_node_spec(branch, {
        "node_id": "judge",
        "display_name": "Judge",
        "source_code": "def run(state):\n    return {}\n",
        "effects": ["authenticated_external_call"],
    })
    assert err == ""
    assert branch.node_defs[0].effects == ["authenticated_external_call"]
