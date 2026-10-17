"""Every router-state opener refuses before touching an owner's private tree."""

import os
from contextlib import ExitStack, closing
from pathlib import Path

import pytest

from tinyassets import owner_runtime
from tinyassets.api_keys import ApiKeyStore
from tinyassets.auth.provider import OAuthProvider
from tinyassets.connection_oauth.pkce import flows_db
from tinyassets.onboarding import (
    approval_handoff,
    canary_sign_in,
    inline_model_connect,
    native_sign_in,
    owner_sessions,
    session_store,
)
from tinyassets.router_credentials import HookOwners


def oauth_connection(root):
    provider = object.__new__(OAuthProvider)
    provider._db_path = root / ".auth.db"
    return closing(provider._connect())


OPENERS = [
    ("owner_sessions", lambda root: owner_sessions.store()),
    ("owner_sessions", lambda root: inline_model_connect.flows()),
    ("owner_sessions", lambda root: approval_handoff.flows()),
    ("refresh_sessions", lambda root: session_store.store_dir()),
    ("native_sign_in", lambda root: native_sign_in.flows()),
    ("canary_sign_in", lambda root: canary_sign_in._limits()),
    ("oauth_pkce", flows_db),
    ("api_keys", lambda root: ApiKeyStore(root).db()),
    ("hook_owners", HookOwners),
    ("mcp_oauth", lambda root: OAuthProvider(root / ".auth.db")),
    ("mcp_oauth", oauth_connection),
]


@pytest.mark.parametrize("name,opener", OPENERS, ids=[
    "owner_sessions", "inline_flows", "handoffs", "refresh_sessions", "native_sign_in",
    "canary_sign_in", "oauth_pkce", "api_keys", "hook_owners", "mcp_oauth", "mcp_oauth_connect",
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
