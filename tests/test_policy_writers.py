"""Ratchet the policy write boundary to the broker and the router's owner channel (N10)."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
#: Owner policy widenings: only the broker applies them, only the router submits them.
_WIDENINGS = {"write", "issue_once", "outside_set"}
#: P0-3: the router's verified caller only (an MCP client's ``full`` row, a held request).
_ROUTER_ONLY = {"outside_admit", "outside_revoke", "held"}
#: The router modules that submit outside-client grants and admissions (P0-3).
_GRANT_ROUTES = {"tinyassets/onboarding/__init__.py", "tinyassets/onboarding/outside_clients.py",
                 "tinyassets/onboarding/api_keys.py", "tinyassets/outside_authority.py",
                 "tinyassets/router_credentials.py"}
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
            # P0-3: only the broker's policy store writes an outside-client grant.
            if "outside_clients" in node.value and any(
                    verb in node.value for verb in ("INSERT", "UPDATE", "DELETE", "CREATE")):
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
            if name == "owner_policy_operation" and (_WIDENINGS | _ROUTER_ONLY) & literal:
                raise AssertionError(f"box-channel policy widening: {relative}:{call.lineno}")
            if (name in {"submit", "call"} and (_WIDENINGS | _ROUTER_ONLY) & literal
                    and relative not in _GRANT_ROUTES):
                raise AssertionError(f"non-router policy widening: {relative}:{call.lineno}")
    assert "policy:write" not in (ROOT / "tinyassets/ta_capabilities.py").read_text()


def test_nothing_on_the_box_channel_writes_an_outside_grant():
    from tinyassets.broker.server import BOX_POLICY_ACTIONS, ROUTER_ONLY, WIDENINGS

    assert not BOX_POLICY_ACTIONS & (WIDENINGS | ROUTER_ONLY)
    assert {"outside_set"} <= WIDENINGS and _ROUTER_ONLY == ROUTER_ONLY
    # The box channel reads a grant; nothing it can send records or changes one.
    assert {"outside_check", "outside_list"} <= BOX_POLICY_ACTIONS


@pytest.mark.parametrize("action", sorted(_WIDENINGS | _ROUTER_ONLY))
def test_the_box_client_carries_no_policy_widening(tmp_path, action):
    from tinyassets.broker.client import BrokerClient

    client = BrokerClient(tmp_path / "absent.sock", principal="alice", command_center="alice",
                          fence=lambda: (0, ""))
    with pytest.raises(ValueError, match="unsupported owner policy operation"):
        client.owner_policy(action, session_proof={"session_hash": "a" * 64})


pytestmark = pytest.mark.verifies(
    'owner-daemon-in-box:C8',
    'owner-daemon-in-box:Owner action policy is held by the broker and enforced at OPEN')
