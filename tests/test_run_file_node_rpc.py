"""Actual sandbox entry/downstream file reads, never forged RPC authority."""

import hashlib

import pytest

from tests.test_run_file_capture import intake  # noqa: F401
from tinyassets import runs
from tinyassets.auth.middleware import identity_context
from tinyassets.branches import BranchDefinition
from tinyassets.run_file_binding import bind_declared_files
from tinyassets.run_file_capture import capture_authoring_files
from tinyassets.storage.run_execution_lock import try_run_execution_lock

SOURCE = """import base64
import hashlib
def run(state):
    digests = []
    for ref in state["FIELD"]:
        digest = hashlib.sha256()
        offset = 0
        while True:
            part = invoke_mcp_action("read_run_file", file_id=ref["file_id"],
                                    offset=offset, count=524288)
            digest.update(base64.b64decode(part["bytes_base64"]))
            offset = part["next_offset"]
            if part["eof"]:
                break
        digests.append(digest.hexdigest())
    return {"RESULT": digests, "forwarded": state["FIELD"]}
"""


@pytest.fixture
def rpc(intake, admitted_center):  # noqa: F811
    base, _, sources, bodies = intake
    admitted_center(base, "u", "owner")
    refs = capture_authoring_files(
        base,
        owner_id="owner",
        universe_id="u",
        label="rpc",
        sources=sources,
    )
    branch = BranchDefinition.from_dict(
        {
            "branch_def_id": "branch",
            "name": "Binary entry and downstream",
            "author": "owner",
            "entry_point": "chosen-entry",
            "node_defs": [
                {
                    "node_id": "first-definition",
                    "display_name": "First",
                    "input_keys": ["files"],
                    "output_keys": ["first", "forwarded"],
                    "tools_allowed": ["read_run_file"],
                    "source_code": SOURCE.replace("FIELD", "files").replace("RESULT", "first"),
                },
                {
                    "node_id": "second-definition",
                    "display_name": "Second",
                    "input_keys": ["forwarded"],
                    "output_keys": ["second"],
                    "tools_allowed": ["read_run_file"],
                    "source_code": SOURCE.replace("FIELD", "forwarded").replace("RESULT", "second"),
                },
            ],
            "graph_nodes": [
                {"id": "chosen-entry", "node_def_id": "first-definition"},
                {"id": "downstream", "node_def_id": "second-definition"},
            ],
            "edges": [
                {"from_node": "chosen-entry", "to_node": "downstream"},
                {"from_node": "downstream", "to_node": "END"},
            ],
            "state_schema": [
                {"name": name, "type": "list"} for name in ["files", "forwarded", "first", "second"]
            ],
            "io_manifest": {
                "inputs": [
                    {
                        "name": name,
                        "io_type": "file_bundle",
                        "max_count": 4,
                        "max_bytes": 4 * 1024 * 1024,
                        "required": name == "files",
                    }
                    for name in ["files", "forwarded"]
                ]
            },
        }
    )
    assert branch.validate() == []
    return base, branch, refs, bodies


def invoke(base, branch, refs):
    run_id = runs._prepare_run(
        base,
        branch=branch,
        inputs={"files": refs},
        run_name="file RPC proof",
        actor="universe:u",
        owner_user_id="owner",
        queue_universe_id="u",
    )
    with runs._connect(base) as conn:
        conn.execute("BEGIN IMMEDIATE")
        bind_declared_files(
            conn,
            run_id=run_id,
            owner_id="owner",
            universe_id="u",
            branch=branch,
            inputs={"files": refs},
        )
    # This lower-level compiler harness supplies the same real guard as the
    # admitted worker; it must not depend on implicit managed-family enrollment.
    with try_run_execution_lock(base, run_id=run_id) as guard, identity_context(None):
        return runs._invoke_graph(
            base,
            run_id=run_id,
            branch=branch,
            inputs={"files": refs},
            provider_call=None,
            recursion_limit=50,
            _execution_guard=guard,
        )


def test_actual_binary_multifile_read_at_chosen_entry_and_downstream(rpc):
    base, branch, refs, bodies = rpc
    outcome = invoke(base, branch, refs)
    assert outcome.status == "completed", outcome.error
    expected = [hashlib.sha256(body).hexdigest() for body in bodies]
    assert outcome.output["first"] == outcome.output["second"] == expected


@pytest.mark.parametrize("selector", ["run_id", "owner_id", "source", "incoming", "node_id"])
def test_actual_rpc_cannot_supply_context_selectors(rpc, selector):
    base, branch, refs, _ = rpc
    branch.node_defs[0].source_code = branch.node_defs[0].source_code.replace(
        "count=524288)",
        f"count=524288, {selector}='forged')",
    )
    outcome = invoke(base, branch, refs)
    assert outcome.status == "failed" and "file_read_parameters" in outcome.error


def test_whole_state_escape_does_not_grant_undeclared_file_input(rpc):
    base, branch, refs, _ = rpc
    branch.node_defs[0].input_keys = []
    branch.node_defs[0].strict_input_isolation = False
    outcome = invoke(base, branch, refs)
    assert outcome.status == "failed" and "declared_node_inputs" in outcome.error


def test_downstream_declaration_limit_is_checked_before_read(rpc):
    base, branch, refs, _ = rpc
    branch.io_manifest["inputs"][1]["max_bytes"] = 1
    outcome = invoke(base, branch, refs)
    assert outcome.status == "failed" and "manifest.invalid_reference@forwarded" in outcome.error
