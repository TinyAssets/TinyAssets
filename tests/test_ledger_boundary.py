"""S30 ledger-boundary: the trusted ledger is the broker's, reached from the owner channel only."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.check_shapes import imported_modules
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


def importers(root: Path = ROOT) -> set[str]:
    """Every module binding a ledger store, relative and literal dynamic imports included."""
    ledger_modules = {path[:-3].replace("/", ".") for path in STORES}
    found = set()
    for path in [*(root / "tinyassets").rglob("*.py"), *(root / "scripts").rglob("*.py")]:
        rel = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source, filename=rel)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for owner, name in STORES.items():
                    assert name not in node.value or rel in {owner, REGISTRY}, (
                        f"{rel} names {name}")
        if imported_modules(rel, source) & ledger_modules and rel not in STORES:
            found.add(rel)
    return found


def test_ledger_boundary(tmp_path):
    assert importers() == IMPORTERS
    _box_channel_refuses_every_ledger_op(tmp_path)


def test_the_guard_sees_relative_and_dynamic_imports(tmp_path):
    probe = tmp_path / "tinyassets" / "broker" / "probe.py"
    probe.parent.mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    for line in ("from . import message_ledger", "from .conversations import Conversations",
                 "import importlib\nimportlib.import_module('tinyassets.broker.message_ledger')"):
        probe.write_text(line + "\n")
        assert importers(tmp_path) == {"tinyassets/broker/probe.py"}, line


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
