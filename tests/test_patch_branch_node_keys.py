"""patch_branch / build_branch input_keys / output_keys coercion.

Bug (2026-04-19): when a caller passed a bare string like
``input_keys="node.output"`` inside a ``changes_json`` update_node op,
``list(op["input_keys"])`` silently expanded it to a per-character list
(``['n','o','d','e',...]``). The node validated fine but was unrunnable.

This suite covers the four write paths where input_keys / output_keys
get persisted into NodeDefinition:

1. ``patch_branch`` update_node op (``changes_json``)
2. ``build_branch`` add_node (``spec_json``) / patch_branch add_node op
3. ``extensions action=add_node`` kwargs shape
4. ``extensions action=update_node`` individual-kwargs shape (vs
   changes_json)

For each path we verify: list input round-trips; JSON-encoded list
round-trips; CSV round-trips; bare token round-trips as a single key;
invalid types / empty entries reject with a clear error (no silent
char-split).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tinyassets.api.branches import _coerce_node_keys
from tinyassets.api.runtime_ops import _apply_patch_ops
from tinyassets.branches import BranchDefinition


@pytest.fixture
def ext_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
            authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    # Branch mutation requires a credential-derived subject. Without one
    # `extensions build_branch` returns
    # `{"error": "Authenticated branch subject required."}` and every test
    # here dies on `KeyError: 'status'` before reaching its own concern.
    # The conftest default (extensions read/write/admin) is exactly what
    # this file needs; `extensions.costly` is deliberately NOT granted.
    authenticate_request("tester")
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, base
    importlib.reload(us)


def _call(us, tool: str, action: str, **kwargs):
    fn = getattr(us, f"_{tool}_impl")
    return json.loads(fn(action=action, **kwargs))


def _build(us, *, node_keys=None) -> str:
    node: dict = {
        "node_id": "capture",
        "display_name": "Capture",
        "prompt_template": "cap: {x}",
    }
    if node_keys is not None:
        node["input_keys"] = node_keys.get("input_keys", [])
        node["output_keys"] = node_keys.get("output_keys", [])
    spec = {
        "name": "b",
        "entry_point": "capture",
        "node_defs": [node],
        "edges": [
            {"from": "START", "to": "capture"},
            {"from": "capture", "to": "END"},
        ],
        "state_schema": [{"name": "x", "type": "str"}],
    }
    res = _call(us, "extensions", "build_branch", spec_json=json.dumps(spec))
    assert res["status"] == "built", res
    return res["branch_def_id"]


def _patch(us, bid: str, ops: list) -> dict:
    return _call(us, "extensions", "patch_branch",
                 branch_def_id=bid, changes_json=json.dumps(ops))


def _load(us, base: Path, bid: str) -> dict:
    from tinyassets.daemon_server import get_branch_definition

    return get_branch_definition(base, branch_def_id=bid)


def _node(branch: dict, nid: str) -> dict:
    for n in branch["node_defs"]:
        if n["node_id"] == nid:
            return n
    raise AssertionError(f"node '{nid}' not on branch")


def _key_atoms(raw) -> list:
    """Normalize a raw input_keys/output_keys value the same way the API does.

    Used by the test scaffolding to declare every key in state_schema so the
    BUG-091 (#924) validator accepts the spec. Returns the coerced key list, or
    an empty list when the value is None / unparseable.
    """
    if raw is None:
        return []
    keys, err = _coerce_node_keys(raw, "input_keys")
    return [] if err else keys


def test_patch_branch_normalizes_string_key_fields_in_persisted_and_preview_paths(
    ext_env,
):
    us, base = ext_env
    bid = _build(us)
    source_branch = BranchDefinition.from_dict(_load(us, base, bid))
    ops = [
        {"op": "add_state_field", "field_name": "goal", "field_type": "str"},
        {"op": "add_state_field", "field_name": "summary", "field_type": "str"},
        {
            "op": "update_node",
            "node_id": "capture",
            "input_keys": "goal",
            "output_keys": '["summary"]',
        },
    ]

    res = _patch(us, bid, ops)
    assert res.get("status") != "rejected", res
    node = _node(_load(us, base, bid), "capture")
    assert node["input_keys"] == ["goal"]
    assert node["output_keys"] == ["summary"]

    preview, err = _apply_patch_ops(source_branch, json.dumps(ops))

    assert err is None
    assert preview is not None
    preview_node = _node(preview.to_dict(), "capture")
    assert preview_node["input_keys"] == ["goal"]
    assert preview_node["output_keys"] == ["summary"]


# ─────────────────────────────────────────────────────────────────────────────
# Core coercion helper — direct unit tests for the branch points.
# ─────────────────────────────────────────────────────────────────────────────


class TestCoerceNodeKeys:

    def test_none_returns_empty(self, ext_env):
        us, _ = ext_env
        keys, err = _coerce_node_keys(None, "input_keys")
        assert keys == []
        assert err == ""

    def test_list_of_strings(self, ext_env):
        us, _ = ext_env
        keys, err = _coerce_node_keys(["a", " b ", "c"], "input_keys")
        assert keys == ["a", "b", "c"]
        assert err == ""

    def test_list_with_empty_entry_rejects(self, ext_env):
        us, _ = ext_env
        keys, err = _coerce_node_keys(["a", "", "c"], "input_keys")
        assert keys == []
        assert "input_keys[1]" in err and "empty" in err

    def test_list_with_non_string_rejects(self, ext_env):
        us, _ = ext_env
        keys, err = _coerce_node_keys(["a", 3], "input_keys")
        assert keys == []
        assert "input_keys[1]" in err and "string" in err

    def test_json_encoded_list(self, ext_env):
        us, _ = ext_env
        keys, err = _coerce_node_keys('["a","b","c"]', "input_keys")
        assert keys == ["a", "b", "c"]
        assert err == ""

    def test_bare_token_single_key(self, ext_env):
        """The original bug: 'node.output' used to char-split."""
        us, _ = ext_env
        keys, err = _coerce_node_keys("node.output", "input_keys")
        assert keys == ["node.output"]
        assert err == ""

    def test_csv_string(self, ext_env):
        us, _ = ext_env
        keys, err = _coerce_node_keys("a, b ,c", "input_keys")
        assert keys == ["a", "b", "c"]
        assert err == ""

    def test_empty_string_returns_empty(self, ext_env):
        us, _ = ext_env
        keys, err = _coerce_node_keys("   ", "input_keys")
        assert keys == []
        assert err == ""

    def test_invalid_type_rejects(self, ext_env):
        us, _ = ext_env
        keys, err = _coerce_node_keys(42, "input_keys")
        assert keys == []
        assert "must be a list or string" in err

    def test_json_non_list_rejects(self, ext_env):
        us, _ = ext_env
        keys, err = _coerce_node_keys('{"a":1}', "input_keys")
        # Starts with { so falls into CSV path, not JSON path — "a":1 is
        # a single bare token. This is documented helper behavior: only
        # strings starting with '[' trigger JSON parsing.
        assert err == ""
        assert keys == ['{"a":1}']

    def test_json_bracketed_but_invalid_rejects(self, ext_env):
        us, _ = ext_env
        keys, err = _coerce_node_keys("[not json", "input_keys")
        assert keys == []
        assert "input_keys" in err and "JSON" in err


# ─────────────────────────────────────────────────────────────────────────────
# patch_branch update_node — the primary bug surface.
# ─────────────────────────────────────────────────────────────────────────────


class TestPatchBranchUpdateNodeKeys:

    def test_update_node_accepts_list(self, ext_env):
        us, base = ext_env
        bid = _build(us)
        # Declare the fields the node will read/write: per BUG-091 (#924) every
        # node input_key / output_key must be a declared state_schema field.
        res = _patch(us, bid, [
            {"op": "add_state_field", "field_name": "alpha", "field_type": "str"},
            {"op": "add_state_field", "field_name": "beta", "field_type": "str"},
            {"op": "add_state_field", "field_name": "gamma", "field_type": "str"},
            {
                "op": "update_node",
                "node_id": "capture",
                "input_keys": ["alpha", "beta"],
                "output_keys": ["gamma"],
            },
        ])
        assert res.get("status") != "rejected", res
        node = _node(_load(us, base, bid), "capture")
        assert node["input_keys"] == ["alpha", "beta"]
        assert node["output_keys"] == ["gamma"]

    def test_update_node_bare_string_is_single_key_not_chars(self, ext_env):
        """Regression: 'node.output' used to become ['n','o','d','e',...]."""
        us, base = ext_env
        bid = _build(us)
        # Declare the field the bare-string key resolves to (BUG-091 / #924).
        res = _patch(us, bid, [
            {"op": "add_state_field", "field_name": "node.output",
             "field_type": "str"},
            {
                "op": "update_node",
                "node_id": "capture",
                "input_keys": "node.output",
            },
        ])
        assert res.get("status") != "rejected", res
        node = _node(_load(us, base, bid), "capture")
        assert node["input_keys"] == ["node.output"], (
            "bare token should round-trip as single key, not char-split"
        )

    def test_update_node_csv_string(self, ext_env):
        us, base = ext_env
        bid = _build(us)
        # Declare the CSV-derived fields (BUG-091 / #924).
        res = _patch(us, bid, [
            {"op": "add_state_field", "field_name": "a", "field_type": "str"},
            {"op": "add_state_field", "field_name": "b", "field_type": "str"},
            {"op": "add_state_field", "field_name": "c", "field_type": "str"},
            {
                "op": "update_node",
                "node_id": "capture",
                "input_keys": "a, b, c",
            },
        ])
        assert res.get("status") != "rejected", res
        node = _node(_load(us, base, bid), "capture")
        assert node["input_keys"] == ["a", "b", "c"]

    def test_update_node_json_encoded_list(self, ext_env):
        us, base = ext_env
        bid = _build(us)
        # Declare the JSON-list-derived fields (BUG-091 / #924).
        res = _patch(us, bid, [
            {"op": "add_state_field", "field_name": "alpha", "field_type": "str"},
            {"op": "add_state_field", "field_name": "beta", "field_type": "str"},
            {
                "op": "update_node",
                "node_id": "capture",
                "input_keys": '["alpha","beta"]',
            },
        ])
        assert res.get("status") != "rejected", res
        node = _node(_load(us, base, bid), "capture")
        assert node["input_keys"] == ["alpha", "beta"]

    def test_update_node_rejects_empty_list_entry(self, ext_env):
        us, _ = ext_env
        bid = _build(us)
        res = _patch(us, bid, [{
            "op": "update_node",
            "node_id": "capture",
            "input_keys": ["a", "", "c"],
        }])
        assert res.get("status") == "rejected", res

    def test_update_node_rejects_non_string_entry(self, ext_env):
        us, _ = ext_env
        bid = _build(us)
        res = _patch(us, bid, [{
            "op": "update_node",
            "node_id": "capture",
            "input_keys": ["a", 5],
        }])
        assert res.get("status") == "rejected", res

    def test_update_node_model_hint_and_llm_policy_round_trip(self, ext_env):
        us, base = ext_env
        bid = _build(us)
        policy = {"preferred": {"provider": "codex", "model": "gpt-5.5"}}

        res = _patch(us, bid, [{
            "op": "update_node",
            "node_id": "capture",
            "model_hint": "checker",
            "llm_policy": policy,
        }])

        assert res.get("status") != "rejected", res
        node = _node(_load(us, base, bid), "capture")
        assert node["model_hint"] == "checker"
        assert node["llm_policy"] == policy

    def test_update_node_patch_op_rejects_invalid_llm_policy(self, ext_env):
        us, _ = ext_env
        bid = _build(us)

        res = _patch(us, bid, [{
            "op": "update_node",
            "node_id": "capture",
            "llm_policy": {"preferred": {}},
        }])

        assert res.get("status") == "rejected", res
        assert "provider" in json.dumps(res)


# ─────────────────────────────────────────────────────────────────────────────
# build_branch / patch_branch add_node — same coercion path via _apply_node_spec.
# ─────────────────────────────────────────────────────────────────────────────


class TestAddNodeSpecKeys:

    def _add_scribe_ops(self, *, input_keys, output_keys=None):
        """Transactional patch: add scribe + wire it reachable from capture."""
        add_op = {
            "op": "add_node",
            "node_id": "scribe",
            "display_name": "Scribe",
            "prompt_template": "write: {x}",
            "input_keys": input_keys,
        }
        if output_keys is not None:
            add_op["output_keys"] = output_keys
        # Per BUG-091 (#924) every node input_key / output_key must be a
        # declared state_schema field. ``x`` is declared by ``_build``; declare
        # any other key the scribe node introduces so the branch validates.
        declared = {"x"}
        decl_ops = []
        for raw in (input_keys, output_keys):
            for key in _key_atoms(raw):
                if key and key not in declared:
                    declared.add(key)
                    decl_ops.append({
                        "op": "add_state_field",
                        "field_name": key,
                        "field_type": "str",
                    })
        # Wire scribe reachable: remove capture->END, add capture->scribe,
        # add scribe->END so branch validates.
        return [
            *decl_ops,
            add_op,
            {"op": "remove_edge", "from": "capture", "to": "END"},
            {"op": "add_edge", "from": "capture", "to": "scribe"},
            {"op": "add_edge", "from": "scribe", "to": "END"},
        ]

    def test_add_node_via_patch_list(self, ext_env):
        us, base = ext_env
        bid = _build(us)
        res = _patch(us, bid, self._add_scribe_ops(
            input_keys=["x"], output_keys=["draft"],
        ))
        assert res.get("status") != "rejected", res
        node = _node(_load(us, base, bid), "scribe")
        assert node["input_keys"] == ["x"]
        assert node["output_keys"] == ["draft"]

    def test_add_node_via_patch_bare_string(self, ext_env):
        us, base = ext_env
        bid = _build(us)
        res = _patch(us, bid, self._add_scribe_ops(
            input_keys="capture.text",
        ))
        assert res.get("status") != "rejected", res
        node = _node(_load(us, base, bid), "scribe")
        assert node["input_keys"] == ["capture.text"], (
            "bare token must round-trip as single key"
        )

    def test_build_branch_rejects_non_string_key(self, ext_env):
        us, _ = ext_env
        spec = {
            "name": "b",
            "entry_point": "capture",
            "node_defs": [{
                "node_id": "capture",
                "display_name": "Capture",
                "prompt_template": "cap: {x}",
                "input_keys": [1, 2],
            }],
            "edges": [
                {"from": "START", "to": "capture"},
                {"from": "capture", "to": "END"},
            ],
            "state_schema": [{"name": "x", "type": "str"}],
        }
        res = _call(us, "extensions", "build_branch",
                    spec_json=json.dumps(spec))
        assert res["status"] == "rejected", res

    def test_build_branch_persists_model_hint_and_llm_policy(self, ext_env):
        us, base = ext_env
        policy = {"preferred": {"provider": "codex", "model": "gpt-5.5"}}
        spec = {
            "name": "b",
            "entry_point": "capture",
            "node_defs": [{
                "node_id": "capture",
                "display_name": "Capture",
                "prompt_template": "cap: {x}",
                "input_keys": ["x"],
                "model_hint": "writer",
                "llm_policy": policy,
            }],
            "edges": [
                {"from": "START", "to": "capture"},
                {"from": "capture", "to": "END"},
            ],
            "state_schema": [{"name": "x", "type": "str"}],
        }

        res = _call(us, "extensions", "build_branch",
                    spec_json=json.dumps(spec))

        assert res["status"] == "built", res
        node = _node(_load(us, base, res["branch_def_id"]), "capture")
        assert node["model_hint"] == "writer"
        assert node["llm_policy"] == policy

    def test_build_branch_rejects_invalid_llm_policy(self, ext_env):
        us, _ = ext_env
        spec = {
            "name": "b",
            "entry_point": "capture",
            "node_defs": [{
                "node_id": "capture",
                "display_name": "Capture",
                "prompt_template": "cap: {x}",
                "input_keys": ["x"],
                "llm_policy": {"preferred": {}},
            }],
            "edges": [
                {"from": "START", "to": "capture"},
                {"from": "capture", "to": "END"},
            ],
            "state_schema": [{"name": "x", "type": "str"}],
        }

        res = _call(us, "extensions", "build_branch",
                    spec_json=json.dumps(spec))

        assert res["status"] == "rejected", res
        assert "provider" in json.dumps(res)

    def test_add_node_rejects_invalid_llm_policy(self, ext_env):
        us, _ = ext_env
        bid = _build(us)
        ops = self._add_scribe_ops(input_keys=["x"], output_keys=["draft"])
        add_op = next(o for o in ops if o["op"] == "add_node")
        add_op["llm_policy"] = {"preferred": {}}

        res = _patch(us, bid, ops)

        assert res.get("status") == "rejected", res
        assert "provider" in json.dumps(res)


# ─────────────────────────────────────────────────────────────────────────────
# extensions action=add_node — CSV/string path.
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# extensions action=update_node — individual-kwargs shape (not changes_json).
# ─────────────────────────────────────────────────────────────────────────────
