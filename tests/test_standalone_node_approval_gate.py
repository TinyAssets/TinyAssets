"""Regression coverage for standalone node approval identity gates."""

from __future__ import annotations


def test_standalone_node_approve_requires_extensions_admin_scope_when_auth_enabled():
    from tinyassets.auth.provider import action_scope_for

    metadata = action_scope_for("extensions", "approve")
    assert metadata is not None
    assert metadata.oauth_scope == "tinyassets.extensions.admin"
    assert metadata.effect == "admin"
