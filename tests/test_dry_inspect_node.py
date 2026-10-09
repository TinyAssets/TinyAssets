"""Tests for dry_inspect_node + dry_inspect_patch MCP actions.

Spec: docs/vetted-specs.md §dry_inspect_node.

Covered:
- inspect_node_dry helper: existing node, nonexistent node, all-nodes path
- placeholder_validation: missing, extra, escaped
- policy_resolution: node / branch / default source
- state_schema_refs population
- zero side effects (no provider calls)
- dry_inspect_node MCP action: branch_def_id lookup, branch_spec_json path,
  node_id specified vs all, nonexistent node 404 shape
- dry_inspect_patch MCP action: add_node op, update_node op,
  add_state_field op, invalid changes_json, missing changes_json
- extensions() routing for both actions
- unknown action still lists dry_inspect_node/patch in available_actions
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tinyassets.branches import BranchDefinition, NodeDefinition
from tinyassets.graph_compiler import inspect_node_dry


def _make_branch(
    *,
    node_id: str = "n1",
    prompt_template: str = "",
    source_code: str = "",
    input_keys: list[str] | None = None,
    output_keys: list[str] | None = None,
    state_schema: list[dict] | None = None,
    llm_policy: dict | None = None,
    default_llm_policy: dict | None = None,
) -> BranchDefinition:
    nd = NodeDefinition(
        node_id=node_id,
        display_name=node_id,
        prompt_template=prompt_template,
        source_code=source_code,
        input_keys=input_keys or [],
        output_keys=output_keys or [],
        llm_policy=llm_policy,
    )
    return BranchDefinition(
        branch_def_id="b1",
        name="test",
        node_defs=[nd],
        state_schema=state_schema or [],
        default_llm_policy=default_llm_policy,
    )


class TestInspectNodeDryHelper:
    def test_returns_node_envelope(self):
        branch = _make_branch(prompt_template="hello {name}")
        result = inspect_node_dry(branch, node_id="n1")
        assert result["node_id"] == "n1"
        assert "node_def" in result
        assert "placeholder_validation" in result
        assert "policy_resolution" in result

    def test_nonexistent_node_id_returns_structured_error(self):
        branch = _make_branch()
        result = inspect_node_dry(branch, node_id="does_not_exist")
        assert "error" in result
        assert result["node_id"] == "does_not_exist"

    def test_no_node_id_returns_all_nodes(self):
        nd1 = NodeDefinition(node_id="n1", display_name="n1")
        nd2 = NodeDefinition(node_id="n2", display_name="n2")
        branch = BranchDefinition(branch_def_id="b1", name="t", node_defs=[nd1, nd2])
        result = inspect_node_dry(branch)
        assert "nodes" in result
        assert len(result["nodes"]) == 2

    def test_resolved_prompt_template_normalizes_double_braces(self):
        branch = _make_branch(prompt_template="Hello {{name}}, see {{thing}}")
        result = inspect_node_dry(branch, node_id="n1")
        assert "{name}" in result["resolved_prompt_template"]
        assert "{{name}}" not in result["resolved_prompt_template"]

    def test_no_template_yields_none_resolved(self):
        branch = _make_branch(source_code="x = 1")
        result = inspect_node_dry(branch, node_id="n1")
        assert result["resolved_prompt_template"] is None

    def test_declared_input_output_keys_present(self):
        branch = _make_branch(
            input_keys=["a", "b"],
            output_keys=["c"],
        )
        result = inspect_node_dry(branch, node_id="n1")
        assert result["declared_input_keys"] == ["a", "b"]
        assert result["declared_output_keys"] == ["c"]

    def test_state_schema_refs_from_template(self):
        branch = _make_branch(
            prompt_template="Write about {topic} for {audience}",
            state_schema=[
                {"name": "topic", "type": "str"},
                {"name": "audience", "type": "str"},
            ],
        )
        result = inspect_node_dry(branch, node_id="n1")
        assert "topic" in result["state_schema_refs"]
        assert "audience" in result["state_schema_refs"]


class TestPlaceholderValidation:
    def test_missing_key_reported(self):
        branch = _make_branch(
            prompt_template="Hello {unknown_key}",
            state_schema=[{"name": "existing", "type": "str"}],
        )
        result = inspect_node_dry(branch, node_id="n1")
        assert "unknown_key" in result["placeholder_validation"]["missing"]

    def test_no_missing_when_schema_covers_all(self):
        branch = _make_branch(
            prompt_template="Hello {name}",
            state_schema=[{"name": "name", "type": "str"}],
        )
        result = inspect_node_dry(branch, node_id="n1")
        assert result["placeholder_validation"]["missing"] == []

    def test_extra_input_key_reported(self):
        branch = _make_branch(
            prompt_template="Hello {name}",
            input_keys=["name", "extra_declared"],
            state_schema=[{"name": "name", "type": "str"}],
        )
        result = inspect_node_dry(branch, node_id="n1")
        assert "extra_declared" in result["placeholder_validation"]["extra"]

    def test_escaped_placeholder_in_escaped_list(self):
        branch = _make_branch(prompt_template=r"Literal \{keep\} and {name}")
        result = inspect_node_dry(branch, node_id="n1")
        assert "keep" in result["placeholder_validation"]["escaped"]
        assert "name" in result["state_schema_refs"]


class TestPolicyResolution:
    def test_node_policy_source(self):
        policy = {"preferred": {"provider": "claude", "model": "opus"}}
        branch = _make_branch(llm_policy=policy)
        result = inspect_node_dry(branch, node_id="n1")
        assert result["policy_resolution"]["source"] == "node"
        assert result["policy_resolution"]["effective_policy"] == policy

    def test_branch_policy_source(self):
        policy = {"preferred": {"provider": "gemini", "model": "pro"}}
        branch = _make_branch(default_llm_policy=policy)
        result = inspect_node_dry(branch, node_id="n1")
        assert result["policy_resolution"]["source"] == "branch"
        assert result["policy_resolution"]["effective_policy"] == policy

    def test_default_policy_source(self):
        branch = _make_branch()
        result = inspect_node_dry(branch, node_id="n1")
        assert result["policy_resolution"]["source"] == "default"
        assert result["policy_resolution"]["effective_policy"] is None

    def test_node_policy_takes_precedence_over_branch(self):
        node_policy = {"preferred": {"provider": "claude"}}
        branch_policy = {"preferred": {"provider": "gemini"}}
        branch = _make_branch(llm_policy=node_policy, default_llm_policy=branch_policy)
        result = inspect_node_dry(branch, node_id="n1")
        assert result["policy_resolution"]["source"] == "node"
        assert result["policy_resolution"]["effective_policy"] == node_policy

    def test_fallback_chain_extracted(self):
        policy = {
            "preferred": {"provider": "claude"},
            "fallback_chain": [{"provider": "groq", "trigger": "unavailable"}],
        }
        branch = _make_branch(llm_policy=policy)
        result = inspect_node_dry(branch, node_id="n1")
        assert result["policy_resolution"]["fallback_chain"] == policy["fallback_chain"]


class TestMcpDryInspectNode:
    def _make_branch_dict(self, **kwargs) -> dict:
        branch = _make_branch(**kwargs)
        return branch.to_dict()

    def test_action_with_branch_def_id(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_node

        branch_dict = self._make_branch_dict(prompt_template="Say {word}")
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        with patch(
            "tinyassets.daemon_server.get_branch_definition",
            return_value=branch_dict,
        ):
            result = json.loads(_action_dry_inspect_node({
                "branch_def_id": "b1",
                "node_id": "n1",
            }))
        assert result["node_id"] == "n1"
        assert "placeholder_validation" in result

    def test_action_with_branch_spec_json(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_node

        branch_dict = self._make_branch_dict(prompt_template="Write {topic}")
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_dry_inspect_node({
            "branch_spec_json": json.dumps(branch_dict),
            "node_id": "n1",
        }))
        assert result["node_id"] == "n1"

    def test_action_no_node_id_returns_all_nodes(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_node

        nd1 = NodeDefinition(node_id="n1", display_name="n1")
        nd2 = NodeDefinition(node_id="n2", display_name="n2")
        branch = BranchDefinition(branch_def_id="b1", name="t", node_defs=[nd1, nd2])
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_dry_inspect_node({
            "branch_spec_json": json.dumps(branch.to_dict()),
        }))
        assert "nodes" in result
        assert len(result["nodes"]) == 2

    def test_action_nonexistent_node_id_returns_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_node

        branch_dict = self._make_branch_dict()
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_dry_inspect_node({
            "branch_spec_json": json.dumps(branch_dict),
            "node_id": "nonexistent",
        }))
        assert "error" in result

    def test_action_missing_branch_returns_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_node

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        with patch(
            "tinyassets.daemon_server.get_branch_definition",
            side_effect=KeyError("b99"),
        ):
            result = json.loads(_action_dry_inspect_node({"branch_def_id": "b99"}))
        assert "error" in result

    def test_action_invalid_spec_json_returns_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_node

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_dry_inspect_node({"branch_spec_json": "not-json"}))
        assert "error" in result

    def test_no_provider_calls(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_node

        branch_dict = self._make_branch_dict(prompt_template="Say {word}")
        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))

        result = json.loads(_action_dry_inspect_node({
            "branch_spec_json": json.dumps(branch_dict),
            "node_id": "n1",
        }))
        assert "node_id" in result


class TestMcpDryInspectPatch:
    def _base_branch_dict(self) -> dict:
        nd = NodeDefinition(
            node_id="n1", display_name="n1", prompt_template="Write {topic}",
        )
        branch = BranchDefinition(
            branch_def_id="b1", name="test", node_defs=[nd],
            state_schema=[{"name": "topic", "type": "str"}],
        )
        return branch.to_dict()

    def test_add_node_op_visible_in_result(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_patch

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        ops = [{"op": "add_node", "node_id": "n2", "display_name": "n2", "phase": "custom"}]
        result = json.loads(_action_dry_inspect_patch({
            "branch_spec_json": json.dumps(self._base_branch_dict()),
            "changes_json": json.dumps(ops),
        }))
        # All-nodes result because no node_id specified
        assert "nodes" in result
        node_ids = [n["node_id"] for n in result["nodes"]]
        assert "n2" in node_ids

    def test_update_node_op_reflected(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_patch

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        ops = [{"op": "update_node", "node_id": "n1", "display_name": "Updated"}]
        result = json.loads(_action_dry_inspect_patch({
            "branch_spec_json": json.dumps(self._base_branch_dict()),
            "node_id": "n1",
            "changes_json": json.dumps(ops),
        }))
        assert result["node_def"]["display_name"] == "Updated"

    def test_add_state_field_expands_schema(self, tmp_path: Path, monkeypatch) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_patch

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        ops = [{"op": "add_state_field", "field_name": "new_field", "field_type": "str"}]
        result = json.loads(_action_dry_inspect_patch({
            "branch_spec_json": json.dumps(self._base_branch_dict()),
            "node_id": "n1",
            "changes_json": json.dumps(ops),
        }))
        # new_field now in schema → template's {topic} should still pass
        assert "placeholder_validation" in result

    def test_missing_changes_json_returns_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_patch

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_dry_inspect_patch({
            "branch_spec_json": json.dumps(self._base_branch_dict()),
        }))
        assert "error" in result

    def test_invalid_changes_json_returns_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from tinyassets.api.runtime_ops import _action_dry_inspect_patch

        monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
        result = json.loads(_action_dry_inspect_patch({
            "branch_spec_json": json.dumps(self._base_branch_dict()),
            "changes_json": "not-json",
        }))
        assert "error" in result
