"""Action scope inside an owner box uses the bound Identity, not a local provider.

The box has no ``UNIVERSE_SERVER_AUTH`` and no ``UNIVERSE_SERVER_DEV_USER``.
``_provider`` stays unset. A scope check must not construct ``DevAuthProvider``.
"""

from __future__ import annotations

import json
import os

import pytest

from tinyassets import owner_runtime
from tinyassets.auth import middleware as auth
from tinyassets.auth.provider import Identity

pytestmark = pytest.mark.verifies("owner-daemon-in-box:T8")

AUTHORIZED = ["read", "write", "costly", "submit_request", "list"]
READ_ONLY = ["read", "list"]


def _admit(monkeypatch, tmp_path):
    monkeypatch.setattr(
        owner_runtime,
        "_runtime",
        owner_runtime.OwnerRuntime("owner", os.getuid(), tmp_path, os.getpid(), None),
    )
    monkeypatch.setattr(auth, "_provider", None)
    monkeypatch.delenv("UNIVERSE_SERVER_AUTH", raising=False)
    monkeypatch.delenv("UNIVERSE_SERVER_DEV_USER", raising=False)

    def create_provider():
        raise AssertionError("owner box instantiated an auth provider")

    monkeypatch.setattr(auth, "create_provider", create_provider)


def _identity(*capabilities: str) -> Identity:
    return Identity(user_id="owner", username="owner", capabilities=list(capabilities))


def test_owner_box_scope_uses_resolve_always_on_the_bound_identity(tmp_path, monkeypatch):
    _admit(monkeypatch, tmp_path)
    allowed = _identity(*AUTHORIZED)
    # The sets engine_mcp_server binds before its local tool calls.
    reading = _identity(*READ_ONLY)
    exact = _identity("tinyassets.extensions.costly")

    with auth.identity_context(allowed):
        assert auth.require_action_scope("extensions", "list_branches") is allowed
        assert auth.require_action_scope("extensions", "run_branch") is allowed
        assert auth.require_action_scope("universe", "inspect") is allowed
        assert auth.require_action_scope("universe", "create_universe") is allowed
        assert auth.require_action_scope("wiki", "read") is allowed
        assert auth.require_action_scope("wiki", "write") is allowed

    with auth.identity_context(reading):
        assert auth.require_action_scope("extensions", "list_branches") is reading
        assert auth.require_action_scope("universe", "list") is reading
        assert auth.require_action_scope("wiki", "search") is reading
        with pytest.raises(PermissionError, match="Missing OAuth scope"):
            auth.require_action_scope("extensions", "run_branch")
        with pytest.raises(PermissionError, match="Missing OAuth scope"):
            auth.require_action_scope("wiki", "write")
        with pytest.raises(PermissionError, match="Missing OAuth scope"):
            auth.require_action_scope("universe", "create_universe")

    with auth.identity_context(exact):
        assert auth.require_action_scope("extensions", "run_branch") is exact
        with pytest.raises(PermissionError, match="Missing OAuth scope"):
            auth.require_action_scope("wiki", "write")

    with auth.identity_context(allowed):
        with pytest.raises(PermissionError, match="No action-scope metadata"):
            auth.require_action_scope("extensions", "not_a_real_action")


def test_dispatch_gates_fail_closed_without_a_provider(tmp_path, monkeypatch):
    from tinyassets.api.workflow_actions import _dispatch_scope_error as extension_scope
    from tinyassets.api.universe import _dispatch_scope_error as universe_scope
    from tinyassets.api.wiki import _dispatch_scope_error as wiki_scope

    _admit(monkeypatch, tmp_path)
    with auth.identity_context(_identity(*READ_ONLY)):
        assert extension_scope("extensions", "list_branches") is None
        assert universe_scope("universe", "inspect") is None
        assert wiki_scope("wiki", "read") is None
        for body in (
            extension_scope("extensions", "run_branch"),
            universe_scope("universe", "create_universe"),
            wiki_scope("wiki", "write"),
        ):
            decoded = json.loads(body)
            assert decoded["auth_scope_required"] is True


def test_receiver_principal_uses_coarse_grants_inside_the_box(tmp_path, monkeypatch):
    from tinyassets.api.receiver_links import _principal
    from tinyassets.storage.receiver_links import ReceiverAccessDenied

    _admit(monkeypatch, tmp_path)
    with auth.identity_context(_identity("read")):
        assert _principal(write=False) == "owner"
        with pytest.raises(ReceiverAccessDenied):
            _principal(write=True)
    with auth.identity_context(_identity("tinyassets.extensions.write")):
        assert _principal(write=True) == "owner"
    with auth.identity_context(None):
        with pytest.raises(ReceiverAccessDenied):
            _principal(write=False)


def test_stray_provider_lookup_fails_closed_inside_the_box(tmp_path, monkeypatch):
    _admit(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="no auth provider"):
        auth._get_provider()


def test_outside_the_box_still_requires_a_named_dev_user(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_runtime, "_runtime", None)
    monkeypatch.setattr(auth, "_provider", None)
    monkeypatch.delenv("UNIVERSE_SERVER_AUTH", raising=False)
    monkeypatch.delenv("UNIVERSE_SERVER_DEV_USER", raising=False)
    with auth.identity_context(_identity(*AUTHORIZED)):
        with pytest.raises(RuntimeError, match="UNIVERSE_SERVER_DEV_USER"):
            auth.require_action_scope("extensions", "list_branches")


def test_receiver_principal_outside_the_box_still_asks_the_provider(monkeypatch):
    from tinyassets.api.receiver_links import _principal
    from tinyassets.storage.receiver_links import ReceiverAccessDenied

    monkeypatch.setattr(owner_runtime, "_runtime", None)

    class Provider:
        def resolve_token(self, token):
            return None

        def is_auth_required(self):
            return False

        def resolve_always_writes(self):
            return True

    monkeypatch.setattr(auth, "_provider", Provider())
    with auth.identity_context(_identity("read")):
        assert _principal(write=False) == "owner"
        with pytest.raises(ReceiverAccessDenied):
            _principal(write=True)
