"""Ratchet the policy write boundary to the broker and owner door."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_policy_writers():
    assert not (ROOT / "tinyassets/agent_rules.py").exists()
    assert not (ROOT / "tinyassets/approval_scopes.py").exists()
    assert not (ROOT / "tinyassets/broker/http_policy.py").exists()
    for path in (ROOT / "tinyassets").rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            fn = call.func
            if isinstance(fn, ast.Attribute) and fn.attr == "write":
                if isinstance(fn.value, ast.Name) and fn.value.id == "store":
                    # The broker still verifies the opaque cookie in its store.
                    if relative == "tinyassets/broker/server.py":
                        continue
                if isinstance(fn.value, ast.Name) and fn.value.id in {
                        "policy_store", "owner_policy"}:
                    raise AssertionError(f"direct policy write: {relative}:{call.lineno}")
            if (isinstance(fn, ast.Name) and fn.id == "owner_policy_operation"
                    and len(call.args) >= 3 and isinstance(call.args[2], ast.Constant)
                    and call.args[2].value == "write"
                    and relative != "tinyassets/onboarding/__init__.py"):
                raise AssertionError(f"non-owner policy write: {relative}:{call.lineno}")
    assert "policy:write" not in (ROOT / "tinyassets/ta_capabilities.py").read_text()
