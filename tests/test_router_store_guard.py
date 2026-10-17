"""Every router-state opener refuses before touching an owner's private tree."""

import ast
import os
from contextlib import ExitStack, closing
from pathlib import Path

import pytest

from tinyassets import owner_runtime
from tinyassets.api_keys import ApiKeyStore
from tinyassets.auth.provider import OAuthProvider
from tinyassets.broker.owner_reads import Plans
from tinyassets.connection_oauth.pkce import flows_db
from tinyassets.onboarding import (
    approval_handoff,
    canary_sign_in,
    inline_model_connect,
    native_sign_in,
    owner_sessions,
    session_store,
)
from tinyassets.router_credentials import DeletedOwners, HookOwners


def oauth_connection(root):
    provider = object.__new__(OAuthProvider)
    provider._db_path = root / ".auth.db"
    return closing(provider._connect())


OPENERS = [
    ("plans", Plans),
    ("owner_sessions", lambda root: owner_sessions.store()),
    ("owner_sessions", lambda root: inline_model_connect.flows()),
    ("owner_sessions", lambda root: approval_handoff.flows()),
    ("refresh_sessions", lambda root: session_store.store_dir()),
    ("native_sign_in", lambda root: native_sign_in.flows()),
    ("canary_sign_in", lambda root: canary_sign_in._limits()),
    ("oauth_pkce", flows_db),
    ("api_keys", lambda root: ApiKeyStore(root).db()),
    ("hook_owners", HookOwners),
    ("deleted_owners", DeletedOwners),
    ("mcp_oauth", lambda root: OAuthProvider(root / ".auth.db")),
    ("mcp_oauth", oauth_connection),
]


@pytest.mark.parametrize("name,opener", OPENERS, ids=[
    "plans", "owner_sessions", "inline_flows", "handoffs", "refresh_sessions", "native_sign_in",
    "canary_sign_in", "oauth_pkce", "api_keys", "hook_owners", "deleted_owners", "mcp_oauth",
    "mcp_oauth_connect",
])
@pytest.mark.parametrize("inside", [True, False], ids=["box", "router"])
def test_router_store_openers(tmp_path, monkeypatch, name, opener, inside):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    runtime = owner_runtime.OwnerRuntime("alice", 1001, tmp_path, os.getpid(), None)
    monkeypatch.setattr(owner_runtime, "_runtime", runtime if inside else None)

    def open_store():
        with ExitStack() as stack:
            opened = opener(tmp_path)
            if not isinstance(opened, Path) and hasattr(opened, "__enter__"):
                stack.enter_context(opened)

    if inside:
        message = f"router store {name} is never opened inside an owner box"
        with pytest.raises(RuntimeError, match=message):
            open_store()
        assert not list(tmp_path.iterdir())
    else:
        open_store()


def test_policy_session_reader_refuses_in_box(tmp_path, monkeypatch):
    from tinyassets.broker.policy import PolicyStore

    monkeypatch.setattr(owner_runtime, "_runtime", owner_runtime.OwnerRuntime(
        "alice", 1001, tmp_path, os.getpid(), None))
    with pytest.raises(RuntimeError, match="router store owner_sessions"):
        PolicyStore(tmp_path, None)._session("alice", {"session_hash": "a" * 64})


# The router can resolve identity, route lifecycle calls and clean up shared
# credentials. These are shared control-plane stores, never owner data stores.
_ROUTER_IMPORTS = {
    "tinyassets.auth.middleware",
    "tinyassets.boxes.daemon_control",
    "tinyassets.broker.owner_identities",
    "tinyassets.onboarding.owner_sessions",
    "tinyassets.router_credentials",
    "tinyassets.storage",  # data_dir is passed only to the shared store APIs.
}


def _router_owner_io(source):
    violations = []
    tree = ast.parse(source)
    dataclass_replace = any(
        isinstance(node, ast.ImportFrom) and node.module == "dataclasses"
        and any(alias.name == "replace" and alias.asname is None for alias in node.names)
        for node in ast.walk(tree)
    )
    for node in ast.walk(tree):
        imports = ([node.module] if isinstance(node, ast.ImportFrom)
                   else [item.name for item in node.names] if isinstance(node, ast.Import) else [])
        for module in imports:
            if module and module.startswith("tinyassets") and module not in _ROUTER_IMPORTS:
                violations.append((node.lineno, "owner import", module))
        if isinstance(node, ast.Call):
            name = (node.func.attr if isinstance(node.func, ast.Attribute)
                    else node.func.id if isinstance(node.func, ast.Name) else "")
            # dataclasses.replace changes identity metadata, never the filesystem.
            if dataclass_replace and isinstance(node.func, ast.Name) and node.func.id == "replace":
                continue
            if name in {"open", "connect", "read_text", "read_bytes", "write_text", "write_bytes",
                        "mkdir", "iterdir", "glob", "rglob", "scandir", "listdir",
                        "unlink", "rmdir", "rename", "replace", "stat", "lstat"}:
                violations.append((node.lineno, "direct filesystem access", name))
    return violations


def test_router_imports_only_shared_control_plane_and_never_opens_files():
    source = (Path(__file__).resolve().parents[1] / "tinyassets" / "owner_router.py").read_text(
        encoding="utf-8",
    )
    assert _router_owner_io(source) == []


@pytest.mark.parametrize("source", [
    "from tinyassets.daemon_server import _connect",
    "import tinyassets.universe_files as files",
    "data_dir().joinpath('owner', 'soul.md').read_text()",
    "open(data_dir() / 'owner' / 'soul.md')",
    "sqlite3.connect(data_dir() / 'owner' / 'history.db')",
])
def test_router_guard_detects_owner_store_imports_and_file_access(source):
    assert _router_owner_io(source)
