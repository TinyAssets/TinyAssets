"""S30 ledger-boundary: the trusted ledger is the broker's, reached from the owner channel only."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_owner_policy import admissions
from tinyassets import rpc_frames as rf
from tinyassets.broker.policy import account_path

ROOT = Path(__file__).resolve().parents[1]
STORES = {"tinyassets/broker/message_ledger.py": "message_ledger.db",
          "tinyassets/broker/conversations.py": "conversations.db"}
#: Classifies on-disk names and never opens them (test_storage_registry_complete).
REGISTRY = "tinyassets/storage_accounting.py"
#: The broker server serves the ops; account erasure only erases.
IMPORTERS = {"tinyassets/broker/server.py", "tinyassets/broker/account_erasure.py"}
OPS = ("OUTSIDE_AGENTS", "ROOM_POST", "MAILBOX_RECEIPT")

pytestmark = pytest.mark.verifies("trusted-message-ledger:The ledger lives in the broker")


def _modules(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if isinstance(node, ast.ImportFrom) and node.module:
        return {node.module} | {f"{node.module}.{alias.name}" for alias in node.names}
    return set()


def test_ledger_boundary(tmp_path):
    ledger_modules = {path[:-3].replace("/", ".") for path in STORES}
    for path in [*(ROOT / "tinyassets").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for owner, name in STORES.items():
                    assert name not in node.value or rel in {owner, REGISTRY}, (
                        f"{rel} names {name}")
            if _modules(node) & ledger_modules and rel not in STORES:
                assert rel in IMPORTERS, f"{rel} imports the ledger"
    _box_channel_refuses_every_ledger_op(tmp_path)


def _box_channel_refuses_every_ledger_op(root):
    from tinyassets.broker.server import BOX, _Connection

    identities = admissions(root)
    server = SimpleNamespace(_owner_identities=identities)

    async def send(doc):
        connection = _Connection(server, None, BOX)
        connection._box_owner = "alice"  # admitted, so only the op itself is refused
        await connection._connection_op(doc["op"], doc)

    for op in OPS:
        with pytest.raises(rf.FrameError, match="not available on the box channel"):
            asyncio.run(send({"op": op, "action": "list", "origin": None}))
    for name in STORES.values():
        assert not account_path(root, "alice", name).exists()
