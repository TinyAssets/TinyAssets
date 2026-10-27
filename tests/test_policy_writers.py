"""Ratchet the policy write boundary to the broker and the router's owner channel (N10)."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
#: Owner policy widenings: only the broker applies them, only the router submits them.
_WIDENINGS = {"write", "issue_once"}
#: Where the broker spends approvals and matches standing grants: its OPEN gate.
_GATE_ONLY = {"consume_once", "standing_grant", "receipt_gate"}


def test_policy_writers():
    assert not (ROOT / "tinyassets/agent_rules.py").exists()
    assert not (ROOT / "tinyassets/approval_scopes.py").exists()
    assert not (ROOT / "tinyassets/broker/http_policy.py").exists()
    assert not (ROOT / "tinyassets/action_policy.py").exists()
    for path in (ROOT / "tinyassets").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if "UPDATE outbound_connections SET owner_egress_json" in node.value:
                assert relative == "tinyassets/broker/http_connect.py", relative
            # No box-local policy store: rules and approvals are broker-held.
            assert ".action-policy.db" not in node.value, relative
            if "TABLE IF NOT EXISTS once_approvals" in node.value:
                assert relative == "tinyassets/broker/policy.py", relative
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            fn = call.func
            if isinstance(fn, ast.Attribute) and fn.attr in _WIDENINGS:
                if isinstance(fn.value, ast.Name) and fn.value.id == "store":
                    # The broker verifies the session proof in its own store.
                    if relative == "tinyassets/broker/server.py":
                        continue
                if isinstance(fn.value, ast.Name) and fn.value.id in {
                        "policy_store", "owner_policy"}:
                    raise AssertionError(f"direct policy write: {relative}:{call.lineno}")
            if isinstance(fn, ast.Attribute) and fn.attr in _GATE_ONLY:
                assert relative == "tinyassets/broker/authority_gate.py", (
                    f"approval spent outside the OPEN gate: {relative}:{call.lineno}")
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            literal = {arg.value for arg in call.args if isinstance(arg, ast.Constant)}
            if name == "owner_policy_operation" and _WIDENINGS & literal:
                raise AssertionError(f"box-channel policy widening: {relative}:{call.lineno}")
            if (name in {"submit", "call"} and _WIDENINGS & literal
                    and relative != "tinyassets/onboarding/__init__.py"):
                raise AssertionError(f"non-router policy widening: {relative}:{call.lineno}")
    assert "policy:write" not in (ROOT / "tinyassets/ta_capabilities.py").read_text()


@pytest.mark.parametrize("action", sorted(_WIDENINGS))
def test_the_box_client_carries_no_policy_widening(tmp_path, action):
    from tinyassets.broker.client import BrokerClient

    client = BrokerClient(tmp_path / "absent.sock", principal="alice", command_center="alice",
                          fence=lambda: (0, ""))
    with pytest.raises(ValueError, match="unsupported owner policy operation"):
        client.owner_policy(action, session_proof={"session_hash": "a" * 64})


pytestmark = pytest.mark.verifies(
    'owner-daemon-in-box:C8',
    'owner-daemon-in-box:Owner action policy is held by the broker and enforced at OPEN')
