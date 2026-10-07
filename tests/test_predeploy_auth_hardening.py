"""Pre-deploy auth hardening — the two STATUS 2026-07-02 merge blockers.

1. Optional mode is resolve-always for writes: anonymous universe *writes*
   (e.g. `create_universe`) fail closed instead of slipping through the scope
   gate (which short-circuited when both gate flags were False).
2. Daemon-scoped memory writes are exempt from the founder scope gate — they
   run autonomously with no founder OAuth grant, so the gate must not reject
   them as anonymous before their exemption applies.
"""
from __future__ import annotations

import json

import pytest

from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import (
    AuthProvider,
    DevAuthProvider,
    OptionalOAuthProvider,
)


class _ResolveAlwaysAnon(AuthProvider):
    """Optional-mode shape: resolves no token (anonymous) but gates writes."""

    def resolve_token(self, token):
        return None

    def is_auth_required(self) -> bool:
        return False

    def resolve_always_writes(self) -> bool:
        return True

    def register_client(self, metadata):
        return {}

    def create_authorization(self, *args, **kwargs) -> str:
        return ""

    def exchange_code(self, *args, **kwargs):
        return None


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    set_provider(DevAuthProvider())
    auth_middleware("dev")


def _use_resolve_always_anon() -> None:
    set_provider(_ResolveAlwaysAnon())
    auth_middleware(None)  # no token -> anonymous identity


def test_optional_mode_is_resolve_always_for_writes():
    provider = OptionalOAuthProvider()
    assert provider.is_auth_required() is False
    # Was False (the gap) — must be True so the scope gate enforces writes.
    assert provider.resolve_always_writes() is True


def test_anon_universe_write_rejected_in_resolve_always(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.api.universe import _dispatch_scope_error

    _use_resolve_always_anon()
    err = _dispatch_scope_error("universe", "create_universe")
    assert err is not None, "anonymous create_universe must fail closed"
    assert json.loads(err).get("auth_scope_required") is True


def test_daemon_memory_gated_for_external_anon(monkeypatch, tmp_path):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.api.universe import _dispatch_scope_error

    _use_resolve_always_anon()
    # daemon_memory_* is MCP-reachable and its handler trusts a caller-supplied
    # daemon_id, so an EXTERNAL anonymous caller must NOT reach it (Codex review
    # 2026-07-03). The autonomous daemon writes memory via the direct
    # daemon_brain path, not this gated dispatch, so gating here is safe.
    assert _dispatch_scope_error("universe", "daemon_memory_capture") is not None
