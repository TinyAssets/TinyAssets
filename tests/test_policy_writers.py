"""Ratchet the owner-authority write boundary to the broker and the router (N10)."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
#: Box-held authority files that are only ever read once, by the exporter.
_OLD_FILES = (".action-policy.db", ".effector_consents.db")
#: The stopped cutover's census and the storage classification name them too.
_NAMES_OLD_FILES = {"tinyassets/authority_export.py", "tinyassets/box_layout_migration.py",
                    "tinyassets/storage_accounting.py"}
_WIDENINGS = {"write", "issue_once", "consent_grant"}


def _calls(tree):
    return (node for node in ast.walk(tree) if isinstance(node, ast.Call))


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
            if any(name in node.value for name in _OLD_FILES):
                assert relative in _NAMES_OLD_FILES, f"box authority store: {relative}"
            if "TABLE IF NOT EXISTS consents" in node.value:
                assert relative == "tinyassets/broker/policy.py", relative
        for call in _calls(tree):
            fn = call.func
            if isinstance(fn, ast.Attribute) and fn.attr in {"write", *_WIDENINGS}:
                if isinstance(fn.value, ast.Name) and fn.value.id == "store":
                    # The broker verifies the session proof in its own store.
                    if relative == "tinyassets/broker/server.py":
                        continue
                if isinstance(fn.value, ast.Name) and fn.value.id in {
                        "policy_store", "owner_policy"}:
                    raise AssertionError(f"direct policy write: {relative}:{call.lineno}")
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            literal = [arg.value for arg in call.args if isinstance(arg, ast.Constant)]
            if (name in {"owner_policy_operation", "_owner_authority_submit", "submit"}
                    and _WIDENINGS & set(literal)
                    and relative != "tinyassets/onboarding/__init__.py"):
                raise AssertionError(f"non-router policy widening: {relative}:{call.lineno}")
    exporter = (ROOT / "tinyassets/authority_export.py").read_text(encoding="utf-8")
    assert '"?mode=ro"' in exporter and "INSERT" not in exporter and "UPDATE" not in exporter
    assert "policy:write" not in (ROOT / "tinyassets/ta_capabilities.py").read_text()


pytestmark = pytest.mark.verifies(
    'owner-daemon-in-box:Owner authority is held by the broker and gated at OPEN')
